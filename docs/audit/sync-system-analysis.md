# 模块二：同步体系（多主站批量同步）分析报告

> 审计时间：2026-08-31
> 分析范围：多主站批量同步 / 三级并行 / 渠道分类内联 / 实时日志 / 双向对账

---

## 一、功能清单对照

| 功能点 | 状态 | 位置 |
|--------|------|------|
| 全局同步配置（一键同步所有主站） | ✅ 已实现 | `SitesPage.vue:271`「同步主站」 |
| 多级并行（三层） | ✅ 已实现 | 见下节 |
| 渠道分类内联 | ✅ 已实现 | `sync_service.py:390` |
| 实时同步日志与状态 | ⚠️ 部分实现 | 前端无流式推送，一次性返回 |
| 浏览器自动同步保存快速响应 | ✅ 已实现 | 400ms 探测 / 200ms 轮询 / 30s |
| 主站同步 = 双向对账 | ✅ 已实现 | `sync_service.py:334-566` |

---

## 二、多级并行结构（三层）

### 第一层：多主站级 `ThreadPoolExecutor`（上限 8）

**位置**：`sync_service.py:1056-1157`（`_run_admin_site_sync`）

```python
max_workers = max(1, min(8, len(valid_admins)))  # 行1101，上限8
if max_workers <= 1:
    for admin in valid_admins:  # 单主站串行（行1103-1104）
        results.append(_sync_one_admin_site(*_sync_args(admin)))
else:  # 多主站并行（行1106-1125）
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(_sync_one_admin_site, ...): id for admin in valid_admins}
        for future in as_completed(future_map):
            results.append(future.result())
results.append({"status": "reconcile", ...})  # 行1127-1156 汇总条目
```

- ✅ `max_workers ≤ 8` 符合要求
- ✅ `as_completed` 按完成顺序收集

### 第二层：单主站 HTTP 级并行（fetch channels + fetch groups）

**位置**：`sync_service.py:982-991`

```python
with ThreadPoolExecutor(max_workers=2) as inner_pool:
    fut_channels = inner_pool.submit(_fetch_channels_task)  # fetch_admin_site_channels
    fut_groups = inner_pool.submit(_fetch_groups_task)      # fetch_admin_site_groups
    ok_ch, raw_channels, ... = fut_channels.result()
    ok_gr, raw_groups, ... = fut_groups.result()
```

- ✅ channels/groups 两个独立 HTTP 并行

### 第三层：数据处理级并行（normalize channels + normalize groups）

**位置**：`sync_service.py:997-1004`

```python
with ThreadPoolExecutor(max_workers=2) as inner_pool:
    (channels, channels_error), (groups, groups_error) = inner_pool.map(
        lambda f: f(), [_norm_channels, _norm_groups]
    )
```

- ✅ 两个纯 CPU normalize 函数并行

### 🔴 潜在问题：嵌套线程池

**问题描述**：

第二层、第三层在**每个主站**内部都新建 `ThreadPoolExecutor(max_workers=2)`。当第一层 8 个主站并行时：

```
8 主站 × (1 HTTP 池(2线程) + 1 normalize 池(2线程)) = 16 个内层线程
外部池又有 8 个线程运行 _sync_one_admin_site
总计峰值并发约 24 个线程
```

这些内层池生命周期极短（仅覆盖 1-2 个请求 + normalize），高频创建/销毁线程池在高峰期开销明显。

**影响**：
- 线程数失控（24+ 并发），可能触发上游限流
- 内层池创建销毁有额外开销

**优化建议**：
- normalize 层其实是纯 CPU 函数，**直接在调用线程内执行即可**（`python` 没有 GIL-free CPU 并行，`ThreadPoolExecutor` 并行纯 Python 计算无收益）。改用串行调用 `normalize_admin_sync_channels / normalize_admin_sync_groups` 即可，省掉第三层线程池。
- HTTP 层（第二层）确有并行收益，保留。若担心线程数，可在 `_sync_one_admin_site` 内排序执行而非再开线程，牺牲少量并行换取线程数受控。

---

## 三、渠道分类内联 ✅ 已实现

**位置**：`sync_service.py:384-441`

```python
# 行388-392：scope 为 recognized/selected 时创建 PlatformDetectService
classifier = PlatformDetectService(...)
# 行390：一次性批量识别所有候选 URL 的平台
classifier.platforms_for_base_urls(...)
# 行394-397：_candidate_platform() 闭包内联过滤
```

- ✅ 分类在 `_sync_admin_site_snapshot_in_connection` 内部完成，**复用同步的同一 HTTP 响应**，无二次请求
- ✅ `scope == "recognized"` 分支过滤出 `SYNCABLE_PLATFORMS`，不符的 URL 记入 `mismatch_urls`
- ✅ 对 `mismatch_urls` 调用 `_delete_platform_mismatch_site_links_in_connection` 清理本地关联（行503-509）

> **需要澄清**：AGENTS.md 提到的「分类内联规则」说分类平台判定在「模块三」。但当前 `PlatformDetectService.platforms_for_base_urls` 是一次性批量识别，与逐 URL 的「模块三」平台判定是否为同一实现需确认。

---

## 四、实时同步日志与状态 ⚠️ 部分实现

### 现状：非流式，一次性返回

**后端**：`sync_service.py:1056-1157`

- `_run_admin_site_sync` 是**同步阻塞** API 调用
- 跑完全部主站后一次性返回 `results` 列表（含 `status: "reconcile"` 汇总条目）

**前端**：

| 页面 | 入口 | 展示方式 |
|------|------|----------|
| `ChannelsPage.vue:782` | `runMainSiteSync` → `handleSyncMainSites` | 等全批完成后一次性 toast 汇总 |
| `SitesPage.vue:108` | `syncAllFromMain` → `syncMainSites()` | `syncResult` 一次性拼出摘要文本 |

**差异点**：
- 功能清单要求「每站点完成即追加状态、刷新列表（不等全批结束）」
- **当前实现不满足**：所有入口都等 `await api.syncMainSites()` 整个请求返回后才展示，后端也没有每站点完成的流式通道（无 SSE/WebSocket/分批返回）

**真正有实时更新的是 Key 刷新批次**（非主站同步）：
- `ChannelsPage.vue:507-564`：`ensureKeyRefreshPolling` 用 1.5 秒 `setInterval`
- 轮询后端 `key_refresh` / `ratio_refresh` 进度（后台 daemon 线程）

### 优化建议

若要满足「每站点完成即追加」：
1. 方案 A：后端改为 SSE 流式返回（每个主站 `as_completed` 时 `yield` 一条）
2. 方案 B：沿用 key 刷新的轮询模式，后端把每主站结果写入内存，前端轮询取增量
3. 方案 C（最简）：前端把 `max_workers ≤ 8` 的主站**逐个 await**，但保持后端并行？——不可行，后端已并行，前端很难拆。

---

## 五、浏览器自动同步保存快速响应 ✅ 已实现

| 参数 | 值 | 位置 |
|------|-----|------|
| 探测超时 | 400ms → 1600ms 二次重试 | `browserSessionBridge.ts:89` |
| 轮询间隔 | 200ms | `browserSessionBridge.ts:327` |
| 最长轮询 | `min(max(8000, expires_in*1000), 30000)` = 30s | `browserSessionBridge.ts:317` |
| SESSION_SYNC_TTL | 120s | `core/state.py:166` |

```typescript
const maxPollMs = Math.min(Math.max(8000, request.expires_in * 1000), 30_000);
// expires_in = 120s → max(8000, 120000)=120000 → min(120000, 30000)=30000
// 实际最长轮询 30 秒
```

- ✅ 保存不卡 30s，探测失败 400ms 即快速失败，扩展在线时走完整桥接
- ✅ `SESSION_SYNC_TTL_SECONDS = 120` 已满足（`session_sync_service.py:164` 创建请求时 `expires_at = now + 120`）

---

## 六、主站同步双向对账 ✅ 已实现

**位置**：`sync_service.py:334-566`（`_sync_admin_site_snapshot_in_connection`，单事务）

| 阶段 | 逻辑 | 位置 |
|------|------|------|
| 1. 渠道候选发现 | `aggregate_newapi_channel_candidates`（仅 NewAPI，按注册域聚合） | 行385 |
| 2. 导入监控站点 | `_import_discovered_site_item`（按 base_url 或 site_merge_key 查找/创建） | 行442-478 |
| 3. discovery_links 对账 | `_reconcile_site_discovery_links_in_connection`（消失即删、同 channel_id 去重） | 行479-486 |
| 4. 清理过时渠道数据 | `_delete_stale_admin_channel_data_in_connection` | 行488-495 |
| 5. 站点状态对账 | `_apply_admin_site_channel_reconcile_in_connection`（无 link 停用/删除、有 link 恢复） | 行499-501 |
| 6. 平台不符清理 | `_delete_platform_mismatch_site_links_in_connection` | 行503-509 |
| 7. 写入同步状态快照 | `INSERT INTO admin_site_sync_state` | 行511-541 |

- ✅ 消失的站点**停用而非删除**（`RECONCILE_MODE_DELETE` 才删除，默认 `RECONCILE_MODE_DISABLE`）
- ✅ 只动 `discovery_links` 里有的
- ✅ 15s 自动 + 手动（`discovery_links` 轮询经 `useConsoleData` 15s 刷新触发）

---

## 七、冗余代码 / 未使用保留功能

### 7.1 后端 Service 类薄包装（死代码）

以下三个类的**实例化**在全局代码中**没有被调用**（类定义存在，但无 `XxxService(...)` 实例化后调用的地方）：

| 类 | 位置 | 说明 |
|----|------|------|
| `SessionSyncService` | `session_sync_service.py:984-989` | 仅包装 `create_site_session_sync_request` / `complete_session_sync_request` 两个顶层函数 |
| `DiscoveryService` | `discovery_service.py:573-578` | 仅包装 `import_sites` / `links` |
| `AdminSiteService` | `admin_site_service.py:558-568` | 注释标明「retained for callers that prefer object-style access」 |

> **核实建议**：上述类是否真的无人实例化需再 grep 一次交叉确认（本次仅按 `rg` 未命中实例化调用）。若确认 0 引用，可删除。

### 7.2 前端三处「同步主站」入口功能重叠

| 位置 | 触发点 | 调用的 API |
|------|--------|-----------|
| `App.vue:51` | `handleSyncMainSites`（全局 action） | `api.syncMainSites(adminSiteId, opts)`（支持范围） |
| `ChannelsPage.vue:782` | `runMainSiteSync`（主站详情页） | `handleSyncMainSites` |
| `SitesPage.vue:108` | `syncAllFromMain`「同步主站」按钮 | `api.syncMainSites()`（无参 = 全局一键） |

三处都调 `api.syncMainSites`，但展示逻辑各不相同，存在**重复展示代码**。`SitesPage.syncAllFromMain` 的汇总文案（新增/恢复/停用/删除）与 `App.vue.handleSyncMainSites` 高度重复。

### 7.3 `find_monitor_site_for_channel` 在同步流程中未被调用

**位置**：`repositories/sites.py:227`

在以下文件被 import，但**仅**用于渠道 key 匹配 / 平台检测的逐渠道场景，**不参与**主站同步的候选导入与 link 对账流程：
- `channel_match_service.py:386`（渠道 key 匹配上游分组）
- `platform_detect_service.py:89`（平台检测）
- `sync_service.py:47`（import 但同步主流程未用它）

### 7.4 `enrich_channel_candidates_with_sites` 用途有限

**位置**：`integrations/newapi.py:141-164`

仅被 `admin_sites` router 的 `channel-candidates` endpoint（`admin_sites.py:205`）调用，为候选列表附加本地站点状态。**不参与同步流程本身**，仅服务前端「同步范围选择弹窗」。

### 7.5 定时调度串行检测站点（设计取舍）

**位置**：`workers/scheduler.py:34-64`

```python
for site in due_sites:
    detect_site(...)  # 逐个串行
```

主站同步用了三级并行，但站点检测是**串行**的。当到期站点很多时较慢。可能是有意设计（避免检测并发打上游被限流），但需确认是否与「同步高性能」目标相冲突。

---

## 八、逻辑问题汇总

| 编号 | 严重度 | 问题 |
|------|--------|------|
| SYNC-001 | 🔴 高 | 嵌套线程池线程数失控（峰值 ~24 线程） |
| SYNC-002 | 🟡 中 | 实时同步日志未实现（前端一次性返回，无流式/逐站追加） |
| SYNC-003 | 🟡 中 | 第三层 normalize 用线程池并行纯 Python 计算无收益（GIL） |
| SYNC-004 | 🟢 低 | 后端 3 个 Service 薄包装类疑似死代码 |
| SYNC-005 | 🟢 低 | 前端 3 处同步主站入口展示逻辑重复 |
| SYNC-006 | 🟢 低 | 定时检测串行 vs 主站同步并行，策略不统一 |
| SYNC-007 | 🟢 低 | `find_monitor_site_for_channel` 在 sync_service import 但主流程未用 |

---

## 九、优化建议

### 优先（P1）
1. **SYNC-001 控制线程数**：合并三层并行或复用线程池，避免 8 主站各建内层池
2. **SYNC-003 去掉 normalize 层线程池**：纯 Python CPU 计算在 GIL 下并行无收益，改串行

### 中期（P2）
3. **SYNC-002 实时日志**：若需满足功能清单，改为 SSE 或轮询增量返回
4. **SYNC-004 清理死类**：确认 0 引用后删除 3 个 Service 薄包装类
5. **SYNC-005 合并前端入口**：抽取公共 composable

### 评估（P3）
6. **SYNC-006 定时检测并行化**：确认是否值得与主站同步统一并行策略

---

## 十、文件索引

### 后端

| 文件 | 相关函数 | 涉及问题 |
|------|----------|----------|
| `backend/services/sync_service.py` | `_run_admin_site_sync` / `_sync_one_admin_site` / `_sync_admin_site_snapshot_in_connection` | SYNC-001/002/003/007 |
| `backend/services/admin_site_service.py` | `fetch_admin_site_channels` / `fetch_admin_site_groups` / `AdminSiteService` | SYNC-001/004 |
| `backend/services/discovery_service.py` | `_import_discovered_site_item` / `_reconcile_site_discovery_links_in_connection` / `DiscoveryService` | SYNC-004 |
| `backend/services/session_sync_service.py` | `SessionSyncService` | SYNC-004 |
| `backend/services/channel_match_service.py` | `match_channel_upstream_binding` | — |
| `backend/services/platform_detect_service.py` | `PlatformDetectService` | — |
| `backend/workers/scheduler.py` | `run_scheduler_tick` | SYNC-006 |
| `backend/integrations/newapi.py` | `enrich_channel_candidates_with_sites` | — |
| `backend/repositories/sites.py` | `normalize_admin_sync_channels` / `normalize_admin_sync_groups` / `find_monitor_site_for_channel` | SYNC-003/007 |

### 前端

| 文件 | 相关函数 | 涉及问题 |
|------|----------|----------|
| `apps/web/src/pages/ChannelsPage.vue` | `runMainSiteSync` / `ensureKeyRefreshPolling` | SYNC-002/005 |
| `apps/web/src/pages/SitesPage.vue` | `syncAllFromMain` | SYNC-005 |
| `apps/web/src/App.vue` | `handleSyncMainSites` | SYNC-005 |
| `apps/web/src/lib/browserSessionBridge.ts` | `probeSessionBridge` / `syncSiteSession` | — |
