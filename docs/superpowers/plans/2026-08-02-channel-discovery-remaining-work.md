# 渠道自动发现后续收尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已经完成的 NewAPI 渠道 URL 自动发现、批量导入和浏览器登录态同步基础上，补齐来源查询、过期关联清理、直接编辑认证、事务一致性以及真实环境验收。

**Architecture:** `app.py` 继续负责 MySQL 来源关系、候选完整性和幂等写入；React 只展示脱敏来源与登录状态，并通过现有 `browserSessionBridge` 发起用户明确触发的同步。所有增强均复用现有 `sites`、`site_discovery_links` 和会话同步协议，不新增第二套认证存储，也不修改快照、diff、余额或通知逻辑。

**Tech Stack:** Python stdlib HTTP + PyMySQL/MySQL、React 19 + TypeScript + Vite/Tailwind、Node test runner、Python `unittest`、Playwright。

---

## 当前完成基线

以下能力已经完成，后续任务不得回退：

- NewAPI 主站全量渠道读取，分页失败或达到安全上限时拒绝返回截断列表。
- 渠道 `base_url` 标准化、合法性校验、去重和来源渠道聚合。
- `GET /api/admin/sites/:id/channel-candidates` 脱敏候选接口。
- `POST /api/sites/discovery-import` 幂等创建/复用监控站点。
- 新站点默认 `platform=newapi`、`enabled=1`、`login_enabled=1`、`auth_mode=browser`。
- 已存在站点不覆盖名称、间隔、认证、快照和变化历史。
- “添加渠道”中的“手动添加 / 从主站发现”双模式。
- 批量选择、监控间隔、顺序同步、进度、登录失败重试和打开登录页。
- 已存在 browser 站点可直接重试；手动 token 站点不会被强制切换认证。
- PriceAI 桌面表格、手机纵向行块、浅色/暗色 token。
- 当前验证：后端非端口测试 187 项通过、前端非端口测试 55 项通过、扩展 27 项通过、生产构建通过。

第一阶段仍只自动发现 NewAPI。sub2api 自动发现不是本计划范围；sub2api 手动添加的浏览器、邮箱/密码兜底和 token 模式必须保留。

## 文件职责

- `app.py`：来源查询、关联清理、事务导入和 HTTP 路由。
- `apps/web/src/lib/types.ts`：候选、来源和导入请求的 TypeScript 契约。
- `apps/web/src/lib/api.ts`：来源查询客户端。
- `apps/web/src/components/ChannelDiscoveryPanel.tsx`：来源显示、直接编辑入口和批量结果状态。
- `apps/web/src/components/SiteFormDialog.tsx`：把发现列表的站点 ID 转交给上层编辑流程。
- `apps/web/src/App.tsx`：按站点 ID 打开已有编辑表单。
- `apps/web/src/pages/DetailPage.tsx`：展示脱敏来源关系。
- `tests/test_channel_discovery_import.py`：后端数据、事务和路由测试。
- `tests/web/channel-discovery-import.test.mjs`：前端契约测试。
- `tests/web/channel-discovery-responsive.test.mjs`：真实渲染与窄屏测试。

### Task 1: 保留真实来源渠道名称并提供来源查询 API

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/components/ChannelDiscoveryPanel.tsx`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `app.py`（`_discovery_public_item()` 附近、`Handler.do_GET()`）
- Test: `tests/test_channel_discovery_import.py`
- Test: `tests/web/channel-discovery-import.test.mjs`

- [ ] **Step 1: 编写后端失败测试**

在 `tests/test_channel_discovery_import.py` 增加：

```python
def test_list_discovery_links_returns_only_public_source_fields(self):
    rows = [{
        "site_id": 7,
        "admin_site_id": 3,
        "admin_site_name": "主站 A",
        "channel_id": 12,
        "channel_name": "主渠道",
        "upstream_base_url": "https://provider.example",
        "created_at": "2026-08-02T10:00:00+08:00",
        "updated_at": "2026-08-02T10:00:00+08:00",
        "access_token": "must-not-leak",
    }]
    with patch.object(app, "db_query_all", return_value=rows):
        result = app.list_site_discovery_links(7)
    self.assertEqual(result[0]["admin_site_name"], "主站 A")
    self.assertEqual(result[0]["channel_name"], "主渠道")
    self.assertNotIn("access_token", result[0])
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
python3 -m unittest tests.test_channel_discovery_import.ChannelDiscoveryImportTests.test_list_discovery_links_returns_only_public_source_fields -v
```

Expected: `AttributeError: module 'app' has no attribute 'list_site_discovery_links'`。

- [ ] **Step 3: 让导入请求携带对应渠道名称**

在 `apps/web/src/lib/types.ts` 修改导入对象：

```ts
export type ChannelDiscoveryImportItem = {
  base_url: string;
  name?: string;
  channel_ids?: number[];
  channel_names?: string[];
};
```

在 `ChannelDiscoveryPanel.tsx` 构造请求时加入：

```ts
items: selectedCandidates.map((candidate) => ({
  base_url: candidate.base_url,
  name: candidate.name,
  channel_ids: candidate.channel_ids,
  channel_names: candidate.channel_names,
})),
```

- [ ] **Step 4: 实现脱敏来源查询**

在 `app.py` 增加：

```python
def list_site_discovery_links(site_id: int) -> List[Dict[str, Any]]:
    rows = db_query_all(
        """
        SELECT l.site_id, l.admin_site_id, a.name AS admin_site_name,
               l.channel_id, l.channel_name, l.upstream_base_url,
               l.created_at, l.updated_at
        FROM site_discovery_links l
        JOIN admin_sites a ON a.id = l.admin_site_id
        WHERE l.site_id = ?
        ORDER BY a.name, l.channel_name, l.channel_id
        """,
        (int(site_id),),
    )
    allowed = (
        "site_id", "admin_site_id", "admin_site_name", "channel_id",
        "channel_name", "upstream_base_url", "created_at", "updated_at",
    )
    return [{key: row.get(key) for key in allowed} for row in rows]
```

在 `Handler.do_GET()` 的通用 `/api/sites/:id` 解析之前增加：

```python
if path.startswith("/api/sites/") and path.endswith("/discovery-links"):
    try:
        site_id = int(path.split("/")[3])
    except (TypeError, ValueError, IndexError):
        return json_response(self, {"success": False, "message": "invalid site id"}, 400)
    site, err, code = get_site_or_404(site_id)
    if err:
        return json_response(self, err, code)
    return json_response(self, {
        "success": True,
        "data": list_site_discovery_links(site_id),
    })
```

- [ ] **Step 5: 增加前端类型与 API 客户端**

在 `types.ts` 增加：

```ts
export type SiteDiscoveryLink = {
  site_id: number;
  admin_site_id: number;
  admin_site_name: string;
  channel_id: number;
  channel_name?: string | null;
  upstream_base_url: string;
  created_at: string;
  updated_at: string;
};
```

在 `api.ts` 增加：

```ts
siteDiscoveryLinks: (siteId: number) =>
  request<{ success: boolean; data: SiteDiscoveryLink[] }>(
    `/api/sites/${siteId}/discovery-links`,
  ),
```

- [ ] **Step 6: 运行测试与构建**

Run:

```bash
python3 -m unittest tests.test_channel_discovery_import -v
node --test tests/web/channel-discovery-import.test.mjs
cd apps/web && npm run build
```

Expected: 全部退出码为 `0`。

- [ ] **Step 7: 提交 Task 1**

```bash
git add app.py apps/web/src/lib/types.ts apps/web/src/lib/api.ts apps/web/src/components/ChannelDiscoveryPanel.tsx tests/test_channel_discovery_import.py tests/web/channel-discovery-import.test.mjs
git commit -m "feat: expose channel discovery provenance"
```

### Task 2: 清理主站中已经不存在的来源关联

**Files:**
- Modify: `app.py`（候选聚合和候选 GET 路由）
- Test: `tests/test_channel_discovery_import.py`

- [ ] **Step 1: 编写关联清理失败测试**

```python
def test_reconcile_discovery_links_removes_only_stale_links(self):
    rows = [
        {"id": 10, "channel_id": 12, "upstream_base_url": "https://a.example"},
        {"id": 11, "channel_id": 18, "upstream_base_url": "https://old.example"},
    ]
    candidates = [{
        "base_url": "https://a.example",
        "channel_ids": [12],
        "channel_names": ["A"],
    }]
    with patch.object(app, "db_query_all", return_value=rows), patch.object(
        app, "db_execute_rowcount", return_value=1
    ) as execute:
        removed = app.reconcile_site_discovery_links(3, candidates)
    self.assertEqual(removed, 1)
    self.assertEqual(execute.call_args.args[1], (11, 3))
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_channel_discovery_import.ChannelDiscoveryImportTests.test_reconcile_discovery_links_removes_only_stale_links -v
```

Expected: 缺少 `reconcile_site_discovery_links`。

- [ ] **Step 3: 实现只删除关联、不删除站点的清理函数**

```python
def reconcile_site_discovery_links(
    admin_site_id: int,
    candidates: List[Dict[str, Any]],
) -> int:
    live_pairs = {
        (int(channel_id), str(candidate.get("base_url") or ""))
        for candidate in candidates
        for channel_id in candidate.get("channel_ids") or []
    }
    rows = db_query_all(
        "SELECT id, channel_id, upstream_base_url "
        "FROM site_discovery_links WHERE admin_site_id = ?",
        (int(admin_site_id),),
    )
    removed = 0
    for row in rows:
        pair = (int(row["channel_id"]), normalize_base_url(row["upstream_base_url"]))
        if pair in live_pairs:
            continue
        removed += db_execute_rowcount(
            "DELETE FROM site_discovery_links WHERE id = ? AND admin_site_id = ?",
            (int(row["id"]), int(admin_site_id)),
        )
    return removed
```

只允许在 `fetch_admin_site_channels(site, "")` 完整成功后调用；分页失败、上游错误或截断时不得清理。

- [ ] **Step 4: 在候选接口中调用清理函数**

```python
candidates = aggregate_newapi_channel_candidates(source_channels)
reconcile_site_discovery_links(admin_site_id, candidates)
candidates = enrich_channel_candidates_with_sites(candidates)
```

- [ ] **Step 5: 验证不会删除站点和历史表**

测试必须断言执行 SQL 只包含：

```text
DELETE FROM site_discovery_links
```

并断言没有出现：

```text
DELETE FROM sites
DELETE FROM snapshots
DELETE FROM changes
```

- [ ] **Step 6: 运行后端回归并提交**

```bash
python3 -m unittest tests.test_channel_discovery_import tests.test_browser_session_sync tests.test_newapi_browser_session_sync -v
git add app.py tests/test_channel_discovery_import.py
git commit -m "feat: reconcile stale discovery links"
```

### Task 3: 在发现结果中直接打开认证编辑

**Files:**
- Modify: `apps/web/src/components/ChannelDiscoveryPanel.tsx`
- Modify: `apps/web/src/components/SiteFormDialog.tsx`
- Modify: `apps/web/src/App.tsx`
- Test: `tests/web/channel-discovery-import.test.mjs`

- [ ] **Step 1: 编写前端契约失败测试**

```js
test("discovery rows can open the imported site authentication editor", async () => {
  const [panel, form, app] = await Promise.all([
    read("apps/web/src/components/ChannelDiscoveryPanel.tsx"),
    read("apps/web/src/components/SiteFormDialog.tsx"),
    read("apps/web/src/App.tsx"),
  ]);
  assert.match(panel, /编辑认证/);
  assert.match(panel, /onEditSite/);
  assert.match(form, /onEditSite/);
  assert.match(app, /sites\.find/);
});
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
node --test tests/web/channel-discovery-import.test.mjs
```

Expected: 找不到 `编辑认证` 或 `onEditSite`。

- [ ] **Step 3: 给发现面板增加回调**

```ts
export function ChannelDiscoveryPanel({
  open,
  onClose,
  onImported,
  onEditSite,
}: {
  open: boolean;
  onClose: () => void;
  onImported: () => Promise<void> | void;
  onEditSite: (siteId: number) => void;
}) {
```

当 `state.siteId` 存在时显示：

```tsx
<Button
  variant="ghost"
  size="sm"
  className="h-8"
  onClick={() => onEditSite(Number(state.siteId))}
>
  编辑认证
</Button>
```

- [ ] **Step 4: 从 SiteFormDialog 向 App 传递站点 ID**

给 `SiteFormDialog` 增加：

```ts
onEditSite: (siteId: number) => void;
```

传递给发现面板：

```tsx
<ChannelDiscoveryPanel
  open
  onClose={() => setMode("manual")}
  onImported={onSaved}
  onEditSite={onEditSite}
/>
```

- [ ] **Step 5: App 按 ID 打开已有编辑表单**

```tsx
<SiteFormDialog
  open={formOpen}
  site={editing}
  onClose={() => setFormOpen(false)}
  onSaved={refresh}
  onEditSite={(siteId) => {
    const target = sites.find((site) => site.id === siteId);
    if (!target) {
      toast.info("渠道列表正在刷新，请稍后重试");
      return;
    }
    setEditing(target);
    setFormOpen(true);
  }}
/>
```

NewAPI 编辑页只允许浏览器同步或普通用户系统令牌 + 用户 ID；不得增加管理员用户名/密码。sub2api 原有可选邮箱/密码兜底保持不变。

- [ ] **Step 6: 运行前端测试和构建并提交**

```bash
node --test tests/web/channel-discovery-import.test.mjs tests/web/browser-session-sync.test.mjs
cd apps/web && npm run build
git add apps/web/src/components/ChannelDiscoveryPanel.tsx apps/web/src/components/SiteFormDialog.tsx apps/web/src/App.tsx tests/web/channel-discovery-import.test.mjs
git commit -m "feat: open authentication editor from discovery"
```

### Task 4: 将每个候选站点的创建与来源关联放进同一事务

**Files:**
- Modify: `app.py`（`import_discovered_sites()`、数据库写入辅助函数）
- Test: `tests/test_channel_discovery_import.py`

- [ ] **Step 1: 编写回滚失败测试**

```python
def test_import_rolls_back_created_site_when_link_insert_fails(self):
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    connection = MagicMock()

    @contextmanager
    def leased_connection():
        yield connection

    with patch.object(app, "db_connection", return_value=leased_connection()), patch.object(
        app,
        "_import_discovered_site_item",
        side_effect=RuntimeError("link failed"),
    ):
        result = app.import_discovered_sites({"id": 3, "platform": "newapi"}, {
            "items": [{
                "base_url": "https://provider.example",
                "name": "Provider",
                "channel_ids": [12],
                "channel_names": ["主渠道"],
            }],
        })
    self.assertEqual(result[0]["status"], "conflict")
    connection.commit.assert_not_called()
    connection.rollback.assert_called_once_with()
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
python3 -m unittest tests.test_channel_discovery_import.ChannelDiscoveryImportTests.test_import_rolls_back_created_site_when_link_insert_fails -v
```

Expected: 当前独立 `db_execute()` 已经提交站点，断言失败。

- [ ] **Step 3: 提取单候选事务函数**

```python
def _import_discovered_site_item(
    connection: pymysql.connections.Connection,
    admin_site_id: int,
    item: Dict[str, Any],
    interval_minutes: int,
) -> Dict[str, Any]:
    base_url = str(item["base_url"])
    name = str(item.get("name") or base_url)[:255]
    now = utc_now_iso()
    with connection.cursor() as cursor:
        cursor.execute(
            _q(
                "SELECT * FROM sites "
                "WHERE base_url = ? OR TRIM(TRAILING '/' FROM base_url) = ? "
                "ORDER BY (base_url = ?) DESC LIMIT 1 FOR UPDATE"
            ),
            (base_url, base_url, base_url),
        )
        existing = cursor.fetchone()
        status = "existing"
        if existing:
            platform = str(existing.get("platform") or "newapi").strip().lower()
            if platform != "newapi":
                return _discovery_public_item(
                    item,
                    "conflict",
                    site_id=existing.get("id"),
                    message="同 URL 已存在非 NewAPI 监控站点",
                )
            site_id = int(existing["id"])
        else:
            cursor.execute(
                _q(
                    """
                    INSERT INTO sites
                    (name, base_url, platform, enabled, interval_minutes,
                     login_enabled, auth_mode, status, next_check_at,
                     created_at, updated_at)
                    VALUES (?, ?, 'newapi', 1, ?, 1, 'browser', 'unknown', ?, ?, ?)
                    """
                ),
                (
                    name,
                    base_url,
                    interval_minutes,
                    next_check_iso(interval_minutes),
                    now,
                    now,
                ),
            )
            site_id = int(cursor.lastrowid)
            status = "created"

        source_names = item.get("channel_names")
        source_names = source_names if isinstance(source_names, list) else []
        for index, channel_id in enumerate(item["channel_ids"]):
            channel_name = (
                str(source_names[index] or "").strip()
                if index < len(source_names)
                else ""
            ) or name
            cursor.execute(
                _q(
                    """
                    INSERT INTO site_discovery_links
                    (site_id, admin_site_id, channel_id, upstream_base_url,
                     channel_name, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE
                      upstream_base_url = VALUES(upstream_base_url),
                      channel_name = VALUES(channel_name),
                      updated_at = VALUES(updated_at)
                    """
                ),
                (
                    site_id,
                    int(admin_site_id),
                    int(channel_id),
                    base_url,
                    channel_name[:255],
                    now,
                    now,
                ),
            )
    return _discovery_public_item(item, status, site_id=site_id)
```

实现中必须满足：

```python
try:
    result = _import_discovered_site_item(connection, admin_site_id, item, interval_minutes)
    connection.commit()
except Exception:
    connection.rollback()
    result = _discovery_public_item(item, "conflict", message="创建或关联监控站点失败")
```

- [ ] **Step 4: 保留并发幂等处理**

如果唯一键竞争导致 `IntegrityError`：

```python
connection.rollback()
existing = _discovery_existing_site(item["base_url"])
```

只有重新读取到同 URL 的 NewAPI 站点时才返回 `existing`；非 NewAPI 返回 `conflict`。

- [ ] **Step 5: 运行数据库与会话回归**

```bash
python3 -m unittest tests.test_channel_discovery_import tests.test_database_performance tests.test_browser_session_sync tests.test_newapi_browser_session_sync -v
```

Expected: 全部通过，且已有认证保留测试继续为 GREEN。

- [ ] **Step 6: 提交 Task 4**

```bash
git add app.py tests/test_channel_discovery_import.py
git commit -m "refactor: make discovery imports transactional"
```

### Task 5: 在站点详情展示发现来源

**Files:**
- Modify: `apps/web/src/pages/DetailPage.tsx`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/lib/types.ts`
- Test: `tests/web/channel-discovery-import.test.mjs`

- [ ] **Step 1: 编写详情页失败测试**

```js
test("site detail renders discovery provenance without credentials", async () => {
  const detail = await read("apps/web/src/pages/DetailPage.tsx");
  assert.match(detail, /发现来源/);
  assert.match(detail, /siteDiscoveryLinks/);
  assert.doesNotMatch(detail, /access_token|login_password|refresh_token/);
});
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
node --test tests/web/channel-discovery-import.test.mjs
```

- [ ] **Step 3: 只在选定站点变化时加载来源**

```ts
const [discoveryLinks, setDiscoveryLinks] = useState<SiteDiscoveryLink[]>([]);

useEffect(() => {
  let cancelled = false;
  if (!site?.id) {
    setDiscoveryLinks([]);
    return;
  }
  api.siteDiscoveryLinks(site.id).then((response) => {
    if (!cancelled) setDiscoveryLinks(response.data || []);
  }).catch(() => {
    if (!cancelled) setDiscoveryLinks([]);
  });
  return () => {
    cancelled = true;
  };
}, [site?.id]);
```

- [ ] **Step 4: 使用 PriceAI 紧凑列表显示来源**

```tsx
{discoveryLinks.length ? (
  <Panel title="发现来源" subtitle={`${discoveryLinks.length} 条来源关联`}>
    <div className="divide-y divide-[var(--color-border-subtle)]">
      {discoveryLinks.map((link) => (
        <div key={`${link.admin_site_id}-${link.channel_id}`} className="grid gap-1 py-2.5 sm:grid-cols-[1fr_1fr_auto] sm:items-center">
          <span className="font-semibold text-[var(--color-text-primary)]">{link.admin_site_name}</span>
          <span className="text-xs text-[var(--color-text-muted)]">{link.channel_name || `渠道 #${link.channel_id}`}</span>
          <code className="break-all text-[11px] text-[var(--color-text-soft)]">{link.upstream_base_url}</code>
        </div>
      ))}
    </div>
  </Panel>
) : null}
```

- [ ] **Step 5: 运行测试、构建并提交**

```bash
node --test tests/web/channel-discovery-import.test.mjs
cd apps/web && npm run build
git add apps/web/src/pages/DetailPage.tsx apps/web/src/lib/api.ts apps/web/src/lib/types.ts tests/web/channel-discovery-import.test.mjs
git commit -m "feat: show channel discovery provenance"
```

### Task 6: 真实浏览器响应式与主题验收

**Files:**
- Create: `tests/web/channel-discovery-responsive.test.mjs`
- Verify: `apps/web/src/components/ChannelDiscoveryPanel.tsx`
- Verify: `apps/web/src/styles/tokens.css`

- [ ] **Step 1: 创建可重复运行的 Playwright 测试**

测试使用 Vite `createServer()`，并通过 `page.route("**/api/**")` 返回固定脱敏数据：

```js
await page.route("**/api/auth/status", (route) => route.fulfill({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify({ auth_required: false, authenticated: true }),
}));

await page.route("**/api/sites", (route) => route.fulfill({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify({ data: [] }),
}));

await page.route("**/api/admin/sites", (route) => route.fulfill({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify({ data: [{
    id: 3,
    name: "主站 A",
    platform: "newapi",
    base_url: "https://admin.example",
  }] }),
}));

await page.route("**/api/admin/sites/3/channel-candidates**", (route) => route.fulfill({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify({ success: true, data: [{
    base_url: "https://provider.example/very-long-provider-path",
    name: "Provider A",
    channel_ids: [12, 18],
    channel_names: ["主渠道", "备用渠道"],
    channel_count: 2,
    existing_site_id: null,
    existing_site_status: null,
    importable: true,
  }] }),
}));
```

- [ ] **Step 2: 检查三个宽度**

依次设置：

```js
for (const viewport of [
  { width: 1440, height: 1000 },
  { width: 375, height: 812 },
  { width: 320, height: 720 },
]) {
  await page.setViewportSize(viewport);
  await page.reload();
  await page.getByRole("button", { name: "添加渠道" }).click();
  await page.getByRole("tab", { name: "从主站发现" }).click();
  await expect(page.getByText("Provider A")).toBeVisible();
  const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
  assert.ok(bodyWidth <= viewport.width);
}
```

- [ ] **Step 3: 检查暗色主题**

```js
await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
await expect(page.getByText("从主站发现 · NewAPI")).toBeVisible();
const panelBackground = await page.locator('[role="dialog"]').evaluate(
  (element) => getComputedStyle(element).backgroundColor,
);
assert.notEqual(panelBackground, "rgb(255, 255, 255)");
```

- [ ] **Step 4: 运行响应式测试**

```bash
node --test tests/web/channel-discovery-responsive.test.mjs
```

Expected: 桌面、375px、320px 和暗色断言全部通过。

- [ ] **Step 5: 提交 Task 6**

```bash
git add tests/web/channel-discovery-responsive.test.mjs apps/web/src/components/ChannelDiscoveryPanel.tsx apps/web/src/styles/tokens.css
git commit -m "test: verify responsive channel discovery UI"
```

### Task 7: MySQL、API 和浏览器扩展真实联调

**Files:**
- Verify: `.env`（仅本地，不提交）
- Verify: `app.py`
- Verify: `extensions/upstream-session-bridge`

- [ ] **Step 1: 启动 MySQL 与后端**

```bash
docker compose up -d mysql
python3 app.py
```

Expected: 后端输出监听地址，且没有 DDL、外键或数据库连接错误。

- [ ] **Step 2: 验证基础 API**

```bash
curl -sS http://127.0.0.1:8000/api/overview
curl -sS http://127.0.0.1:8000/api/sites
```

Expected: 两个接口均返回 JSON；启用控制台密码时使用当前会话的 Bearer token，不在命令历史或文档中写入真实 token。

- [ ] **Step 3: 验证候选接口**

```bash
curl -sS http://127.0.0.1:8000/api/admin/sites/3/channel-candidates
```

Expected:

- 相同规范化 URL 只出现一次。
- `channel_ids` 和 `channel_names` 一一对应。
- 不出现 `access_token`、`refresh_token`、Cookie 或密码。
- `meta.source_channel_total` 等于主站实际渠道数量。

- [ ] **Step 4: 验证重复导入不覆盖数据**

连续两次提交相同候选；第一次应返回 `created` 或 `existing`，第二次返回 `existing`。随后检查：

```sql
SELECT id, name, interval_minutes, auth_mode, created_at
FROM sites
WHERE base_url = 'https://provider.example';
```

Expected: 只有一行，手动修改过的名称、间隔和认证模式保持不变。

- [ ] **Step 5: 验证浏览器状态矩阵**

逐项执行：

1. 已打开并登录同 Origin 页面：状态变为 `ready`，首次检测成功。
2. 已打开但未登录：状态变为 `no_session`，站点仍保留。
3. 未打开目标 Origin：显示没有可用登录态，不后台打开标签页。
4. 扩展未连接：状态变为 `extension_unavailable`。
5. 登录后点击“重新同步”：状态从失败/未登录转为 `ready`。

全过程不得读取 Chrome 密码库、Profile 或 DevTools 远程调试端口。

- [ ] **Step 6: 验证账户与监控能力没有回退**

- NewAPI `/api/user/self` 账户额度可查询。
- 定时检测和手动检测可用。
- 快照、倍率 diff、模型上下架 diff 正常写入。
- 邮件与企业微信配置可保存并测试发送。
- 不恢复 QQ 推送。

### Task 8: 最终全套验证与交付

**Files:**
- Verify: `app.py`
- Verify: `apps/web`
- Verify: `extensions/upstream-session-bridge`
- Verify: `tests/`

- [ ] **Step 1: Python 语法检查**

```bash
PYTHONPYCACHEPREFIX=/tmp/upstream-pycache python3 -m py_compile app.py
```

Expected: 退出码 `0`。

- [ ] **Step 2: 全量后端测试**

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: 所有测试通过；本地端口受限环境应在拥有端口权限的真实终端重跑 `test_http_proxy_refresh.py`。

- [ ] **Step 3: 全量前端与扩展测试**

```bash
node --test tests/web/*.test.mjs
node --test extensions/upstream-session-bridge/tests/*.test.js
cd apps/web && npm run build
```

Expected: 所有测试通过，Vite 生产构建成功。

- [ ] **Step 4: 安全检查**

```bash
git diff --check
rg -n "access_token|refresh_token|login_password|browser_refresh_cookie" apps/web/src/components/ChannelDiscoveryPanel.tsx tests/web/channel-discovery-responsive.test.mjs
```

Expected: `git diff --check` 无输出；发现面板和测试 fixture 中不出现真实凭据值。

- [ ] **Step 5: 数据兼容检查**

确认：

- 没有 `DROP TABLE`、`TRUNCATE` 或清空 `sites` 的迁移。
- `data/app.db` 和备份未修改。
- `site_discovery_links` 使用增量 `CREATE TABLE IF NOT EXISTS`。
- 删除来源关联不会删除本地监控站点或历史。

- [ ] **Step 6: 最终提交**

```bash
git add app.py apps/web/src tests docs/superpowers/plans/2026-08-02-channel-discovery-remaining-work.md
git commit -m "feat: complete channel discovery workflow"
```

## 完成定义

只有同时满足以下条件才可宣布后续收尾完成：

- 发现来源可在 API 和站点详情中查询，且不泄漏认证信息。
- 主站渠道删除后只清理来源关联，不删除监控站点和历史。
- 发现列表可以直接打开已有站点认证编辑。
- 创建站点与来源写入具备单候选事务一致性。
- 1440px、375px、320px 和暗色主题自动化检查通过。
- 真实 MySQL、NewAPI 主站和 Chrome 扩展状态矩阵验证通过。
- 后端、前端、扩展和生产构建全部为绿色。
