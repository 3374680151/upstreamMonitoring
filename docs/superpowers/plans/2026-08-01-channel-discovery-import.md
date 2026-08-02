# 主站渠道 URL 自动发现与监控导入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从选定的 NewAPI 主站发现去重后的渠道 URL，在添加渠道流程中幂等导入本地监控站点，并逐项复用现有浏览器登录态同步，同时保持所有原有监控能力。

**Architecture:** 后端新增候选聚合、来源关联表和批量导入端点；前端在 `SiteFormDialog` 内增加“从主站发现”视图，导入后按站点顺序调用现有 `browserSessionBridge`。不修改检测器和会话协议，所有敏感凭据仍只在后端/扩展之间流转。

**Tech Stack:** Python stdlib HTTP + PyMySQL/MySQL, React 19 + TypeScript + Vite/Tailwind, Node built-in test runner, Python `unittest`.

---

### Task 1: 候选聚合与来源关联数据层

**Files:**
- Modify: `app.py` (`DDL_STATEMENTS`, incremental schema additions, candidate helpers)
- Create: `tests/test_channel_discovery_import.py`

- [ ] **Step 1: Write the failing tests**

Add tests for URL grouping and the new table contract:

```python
def test_group_newapi_channels_by_normalized_base_url():
    channels = [
        {"id": 12, "name": "A", "base_url": "https://provider.example/"},
        {"id": 18, "name": "A backup", "base_url": "https://provider.example"},
        {"id": 21, "name": "B", "base_url": "https://other.example/api"},
        {"id": 22, "name": "invalid", "base_url": "ftp://bad.example"},
    ]
    result = app.aggregate_newapi_channel_candidates(channels)
    assert result == [
        {
            "base_url": "https://provider.example",
            "name": "A",
            "channel_ids": [12, 18],
            "channel_names": ["A", "A backup"],
            "channel_count": 2,
        },
        {
            "base_url": "https://other.example/api",
            "name": "B",
            "channel_ids": [21],
            "channel_names": ["B"],
            "channel_count": 1,
        },
    ]

def test_discovery_link_ddl_is_incremental_and_cascades_local_rows():
    ddl = next(statement for statement in app.DDL_STATEMENTS
               if "site_discovery_links" in statement)
    assert "REFERENCES sites(id) ON DELETE CASCADE" in ddl
    assert "REFERENCES admin_sites(id) ON DELETE CASCADE" in ddl
    assert "UNIQUE KEY uq_site_discovery_channel" in ddl
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_channel_discovery_import -v`

Expected: failure because `aggregate_newapi_channel_candidates` and the new DDL are absent.

- [ ] **Step 3: Implement the minimal data-layer helpers**

Add a `site_discovery_links` `CREATE TABLE IF NOT EXISTS` statement to `DDL_STATEMENTS`, and add:

```python
def aggregate_newapi_channel_candidates(channels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for channel in channels:
        base_url = normalize_base_url(str(channel.get("base_url") or ""))
        parsed = urlparse(base_url)
        if not base_url or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            continue
        item = grouped.setdefault(base_url, {
            "base_url": base_url,
            "name": str(channel.get("name") or "").strip() or base_url,
            "channel_ids": [],
            "channel_names": [],
        })
        if channel.get("id") not in item["channel_ids"]:
            item["channel_ids"].append(channel.get("id"))
        name = str(channel.get("name") or "").strip()
        if name:
            item["channel_names"].append(name)
    return [
        {**item, "channel_count": len(item["channel_ids"])}
        for item in grouped.values()
    ]
```

Keep ordering stable as returned by the upstream channel list. Use the existing `ensure_schema()` migration path; do not drop or rebuild tables.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_channel_discovery_import -v`

Expected: all data-layer tests pass.

### Task 2: Candidate API and idempotent import endpoint

**Files:**
- Modify: `app.py` (candidate payload, import transaction, `Handler.do_GET`, `Handler.do_POST`)
- Modify: `tests/test_channel_discovery_import.py`

- [ ] **Step 1: Write failing endpoint tests**

Cover existing-site joining and import idempotency without touching a real database:

```python
def test_enrich_candidates_marks_existing_site_without_returning_credentials():
    candidates = [{"base_url": "https://provider.example", "name": "A", "channel_ids": [12], "channel_names": ["A"], "channel_count": 1}]
    with patch.object(app, "db_query_all", return_value=[{
        "id": 7, "base_url": "https://provider.example", "status": "ok",
        "session_sync_status": "ready", "access_token": "secret",
    }]):
        result = app.enrich_channel_candidates_with_sites(candidates)
    assert result[0]["existing_site_id"] == 7
    assert result[0]["existing_site_status"] == "ready"
    assert "access_token" not in result[0]

def test_import_candidates_reuses_existing_site_and_writes_link():
    with patch.object(app, "db_query_one", side_effect=[None, {"id": 7}]), \
         patch.object(app, "db_execute", return_value=7) as execute:
        result = app.import_discovered_sites(
            {"id": 3, "platform": "newapi"},
            {"interval_minutes": 3, "items": [{
                "base_url": "https://provider.example/",
                "name": "Provider A",
                "channel_ids": [12],
            }]},
        )
    assert result[0]["site_id"] == 7
    assert result[0]["status"] in {"created", "existing"}
    assert any("site_discovery_links" in call.args[0] for call in execute.call_args_list)
```

- [ ] **Step 2: Run endpoint tests and verify RED**

Run: `python3 -m unittest tests.test_channel_discovery_import -v`

Expected: failure because candidate enrichment/import functions and routes are absent.

- [ ] **Step 3: Implement candidate enrichment and import**

Implement:

```python
def enrich_channel_candidates_with_sites(candidates):
    rows = db_query_all("SELECT id, base_url, status, session_sync_status FROM sites")
    by_url = {normalize_base_url(str(row.get("base_url") or "")): row for row in rows}
    result = []
    for candidate in candidates:
        row = by_url.get(candidate["base_url"])
        result.append({
            **candidate,
            "existing_site_id": row.get("id") if row else None,
            "existing_site_status": (
                row.get("session_sync_status") or row.get("status") or "unknown"
            ) if row else None,
            "importable": True,
        })
    return result
```

`import_discovered_sites()` must validate the admin row is NewAPI, normalize every submitted URL, cap item count, create missing `sites` with `platform='newapi'`, `enabled=1`, `login_enabled=1`, `auth_mode='browser'`, and write `site_discovery_links` with an upsert. Never overwrite existing credentials, interval, name, snapshots, or changes. Return per-item `created`, `existing`, `invalid`, or `conflict` results.

Add `GET /api/admin/sites/:id/channel-candidates` before the generic admin channel route. It must call `fetch_admin_site_channels()` with an empty keyword, aggregate, enrich, and return `meta.source_channel_total`.

Add `POST /api/sites/discovery-import` before `/api/sites/:id` parsing. It must call `get_admin_site_or_404()`, `import_discovered_sites()`, and return item results. Keep console auth guard active.

- [ ] **Step 4: Run endpoint tests and the existing backend suite**

Run: `python3 -m unittest tests.test_channel_discovery_import tests.test_browser_session_sync tests.test_newapi_browser_session_sync -v`

Expected: all focused and browser-session regression tests pass.

### Task 3: Frontend types, API client, and discovery view

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`
- Create: `apps/web/src/components/ChannelDiscoveryPanel.tsx`
- Create: `tests/web/channel-discovery-import.test.mjs`

- [ ] **Step 1: Write failing source-contract tests**

Assert the API paths, candidate fields, and PriceAI UI states:

```js
test("discovery client exposes candidates and idempotent import", async () => {
  const apiSource = await read("apps/web/src/lib/api.ts");
  const typeSource = await read("apps/web/src/lib/types.ts");
  const panelSource = await read("apps/web/src/components/ChannelDiscoveryPanel.tsx");
  assert.match(apiSource, /channelCandidates/);
  assert.match(apiSource, /\/api\/admin\/sites\/\$\{adminSiteId\}\/channel-candidates/);
  assert.match(apiSource, /discovery-import/);
  assert.match(typeSource, /export type ChannelDiscoveryCandidate/);
  assert.match(panelSource, /从主站发现/);
  assert.match(panelSource, /添加并同步/);
  assert.match(panelSource, /session_sync_status|existing_site_status/);
});
```

- [ ] **Step 2: Run the UI contract test and verify RED**

Run: `node --test tests/web/channel-discovery-import.test.mjs`

Expected: failure because the candidate types, API methods, and component do not exist.

- [ ] **Step 3: Add typed client methods and the responsive PriceAI panel**

Add `ChannelDiscoveryCandidate`, `ChannelDiscoveryImportItem`, and `ChannelDiscoveryImportResult` types. Add:

```ts
channelCandidates: (adminSiteId: number, keyword = "") =>
  request<{ success: boolean; data?: ChannelDiscoveryCandidate[]; meta?: Record<string, number> }>(
    `/api/admin/sites/${adminSiteId}/channel-candidates?keyword=${encodeURIComponent(keyword)}`,
  ),
importDiscoveredSites: (payload: { admin_site_id: number; interval_minutes: number; items: ChannelDiscoveryImportItem[] }) =>
  request<{ success: boolean; data?: ChannelDiscoveryImportResult[] }>(
    "/api/sites/discovery-import",
    { method: "POST", body: JSON.stringify(payload) },
  ),
```

`ChannelDiscoveryPanel` owns only local selection/filter state and receives `adminSites`, `onImported`, and `onClose` callbacks. Render a segmented mode header, admin-site select, compact KPI row, dense table, status badges, and a bottom action row. Use existing `Panel`, `Badge`, `Button`, `Input`, `Select`, `Spinner`, and CSS tokens. Do not put secrets in state or props. Disable already-imported rows only when no new link can be created; allow opening an existing row for editing.

Use `useMemo` for filtered candidates and a `Map`/`Set` for selected IDs. Fetch candidates only when the discovery mode is opened or the selected admin site changes; use `Promise.all` only for independent refreshes. Keep all text wrapping at mobile widths.

- [ ] **Step 4: Run the UI contract test and build**

Run: `node --test tests/web/channel-discovery-import.test.mjs && cd apps/web && npm run build`

Expected: contract test and TypeScript/Vite build pass.

### Task 4: Integrate import orchestration into the existing add flow

**Files:**
- Modify: `apps/web/src/components/SiteFormDialog.tsx`
- Modify: `apps/web/src/pages/SitesPage.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/components/SiteTable.tsx` only if status refresh wiring is required
- Modify: `tests/web/channel-discovery-import.test.mjs`

- [ ] **Step 1: Write failing integration assertions**

Add source checks for mode switching, draft preservation, sequential sync, and retry states:

```js
test("add flow keeps manual mode and orchestrates imported browser sessions", async () => {
  const [form, appSource, page] = await Promise.all([
    read("apps/web/src/components/SiteFormDialog.tsx"),
    read("apps/web/src/App.tsx"),
    read("apps/web/src/pages/SitesPage.tsx"),
  ]);
  assert.match(form, /手动添加/);
  assert.match(form, /从主站发现/);
  assert.match(appSource, /ChannelDiscoveryPanel/);
  assert.match(page, /channelCandidates|discovery/);
  assert.match(form, /syncSiteBrowserSession/);
});
```

- [ ] **Step 2: Run the integration test and verify RED**

Run: `node --test tests/web/channel-discovery-import.test.mjs`

Expected: failure because the current form has no discovery mode or import orchestration.

- [ ] **Step 3: Implement the integration without changing existing manual behavior**

Keep the current manual form state and validation untouched. Add an explicit mode state (`manual | discovery`) and render `ChannelDiscoveryPanel` when discovery is selected. On import:

1. Call `api.importDiscoveredSites()` once for the selected candidates.
2. For each returned newly created or existing site that is in browser mode, call `syncSiteBrowserSession(siteId)` sequentially; update only that row’s status.
3. Refresh `api.sites()` after each terminal result or at batch completion.
4. Keep failed/no-session rows open with “打开上游登录页” and “重新同步”; do not close the manual form unexpectedly.
5. Preserve all existing `onSaved`, delete, check, balance, snapshot, notification, and automatic-refresh callbacks.

Use functional state updates and stable callbacks; do not define large inline components inside `SitesPage` or `App`. Keep network calls in event handlers/effects, not render paths.

- [ ] **Step 4: Run all frontend tests and build**

Run: `node --test tests/web/*.test.mjs && cd apps/web && npm run build`

Expected: all existing and new frontend tests pass, with no TypeScript errors.

### Task 5: Verification and local integration

**Files:**
- Verify: `app.py`, `apps/web`, `extensions/upstream-session-bridge`, `tests/`

- [ ] **Step 1: Run Python syntax and focused backend tests**

Run: `python3 -m py_compile app.py && python3 -m unittest discover -s tests -p 'test_*.py' -v`

Expected: exit 0 with zero failing tests.

- [ ] **Step 2: Run extension tests and production build**

Run: `node --test extensions/upstream-session-bridge/tests/*.test.js && cd apps/web && npm run build`

Expected: all extension tests pass and `dist` builds successfully.

- [ ] **Step 3: Verify API routes against a running local backend**

Start with the project’s normal command (`python3 app.py`) using the existing local `.env`; verify `GET /api/overview` and `GET /api/sites` still return JSON. With a configured NewAPI admin site, verify the candidate endpoint returns deduplicated URLs and that an import request does not duplicate an existing `sites.base_url`.

- [ ] **Step 4: Verify responsive UI states**

Run the frontend dev server and use Playwright at desktop, 375px, and 320px widths. Confirm the add dialog, discovery table, selection toolbar, progress state, no-session state, and retry actions are visible, non-overlapping, and usable in both themes. Capture screenshots for review; do not claim completion until the build and tests are fresh and green.

