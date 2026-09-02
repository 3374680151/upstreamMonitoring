# 模块二：同步体系 - 审计问题清单

> 审计时间：2026-08-31
> 关联文档：[完整分析报告](./sync-system-analysis.md)

---

## 问题总览

| 编号 | 严重度 | 状态 | 问题摘要 |
|------|--------|------|----------|
| SYNC-001 | 🔴 高 | ✅ 已处理（2026-09-03，HTTP 池保留） | 嵌套线程池线程数失控 |
| SYNC-002 | 🟡 中 | 待确认 | 实时同步日志未实现（无流式/逐站追加） |
| SYNC-003 | 🟡 中 | ✅ 已修复（2026-09-03） | normalize 层用线程池并行纯 Python 计算无收益 |
| SYNC-004 | 🟢 低 | ✅ 已修复（2026-09-03） | 后端 3 个 Service 薄包装类疑似死代码 |
| SYNC-005 | 🟢 低 | ◐ 部分解决（ChannelsPage 侧已收敛） | 前端 3 处同步主站入口展示逻辑重复 |
| SYNC-006 | 🟢 低 | 待评估 | 定时检测串行 vs 主站同步并行，策略不统一 |
| SYNC-007 | 🟢 低 | ✅ 已修复（2026-09-03） | `find_monitor_site_for_channel` 在 sync_service import 但主流程未用 |

---

## 🔴 高风险问题

### SYNC-001：嵌套线程池线程数失控

**位置**：`backend/services/sync_service.py:987-991, 1001-1004`

**问题描述**：

`_sync_one_admin_site` 内每次新建两个 `ThreadPoolExecutor(max_workers=2)`（一个管 HTTP 并行、一个管 normalize 并行）。当顶层 8 个主站并行时：

```
8 主站 × (HTTP池 2线程 + normalize池 2线程) = 16 内层线程
+ 顶层池 8 个 _sync_one_admin_site 线程
= 峰值约 24 个线程并发
```

内层池生命周期极短，高频创建/销毁有额外开销，且线程数可能触发上游限流。

**修复建议**：

- 去掉 normalize 层线程池（纯 CPU 计算在 GIL 下并行无收益），改串行
- HTTP 层并行如果保留，考虑在 `_sync_one_admin_site` 内**直接顺序等待**或用轻量方式，避免每主站各建一个池

**涉及文件**：`backend/services/sync_service.py`

---

## 🟡 中风险问题

### SYNC-002：实时同步日志未实现

**位置**：`apps/web/src/pages/ChannelsPage.vue:782` / `apps/web/src/pages/SitesPage.vue:108`

**功能清单要求**：每站点完成即追加状态、刷新列表（不等全批结束）

**当前实现**：前端 `await api.syncMainSites()` 整个请求返回后才一次性展示，后端 `_run_admin_site_sync` 也是同步阻塞跑完全部主站才返回。**无流式通道**（无 SSE/WebSocket/增量返回）。

**待确认**：
- 是否必须满足「每站点完成即追加」？若要满足需改造为 SSE 或轮询增量。

**建议方案**：
1. 后端 SSE 流式返回（每个主站 `as_completed` 时 yield 一条）
2. 或复用 key 刷新的轮询模式

**涉及文件**：`backend/services/sync_service.py`、`apps/web/src/pages/ChannelsPage.vue`、`apps/web/src/pages/SitesPage.vue`

---

### SYNC-003：normalize 层用线程池并行纯 Python 计算无收益

**位置**：`backend/services/sync_service.py:997-1004`

**问题描述**：

`normalize_admin_sync_channels` / `normalize_admin_sync_groups` 是纯 Python CPU 函数。CPython 的 GIL 限制下，`ThreadPoolExecutor` 并行纯 Python 计算**没有真实并行收益**，只会增加线程切换开销。

**修复建议**：

```python
# 改回串行
channels, channels_error = normalize_admin_sync_channels(raw_channels)
groups, groups_error = normalize_admin_sync_groups(raw_groups)
```

**涉及文件**：`backend/services/sync_service.py`

---

## 🟢 低风险问题

### SYNC-004：后端 3 个 Service 薄包装类疑似死代码

**位置**：
- `session_sync_service.py:984-989`（`SessionSyncService`）
- `discovery_service.py:573-578`（`DiscoveryService`）
- `admin_site_service.py:558-568`（`AdminSiteService`）

**问题描述**：

三个类都只是顶层函数的薄包装。全局 grep 未命中实例化调用（`XxxService(...)`），疑似无调用方。

**修复建议**：
- 交叉确认 0 引用后删除
- 若不删除，确认是否有调用方依赖对象式访问

**涉及文件**：上述三个 service 文件

---

### SYNC-005：前端 3 处同步主站入口展示逻辑重复

**位置**：
- `apps/web/src/App.vue:51`（`handleSyncMainSites`）
- `apps/web/src/pages/ChannelsPage.vue:782`（`runMainSiteSync`）
- `apps/web/src/pages/SitesPage.vue:108`（`syncAllFromMain`）

**问题描述**：

三处都调 `api.syncMainSites`，但各自写了独立的汇总文案/状态展示，逻辑重复。

**修复建议**：
- 抽取公共 composable（如 `useMainSiteSync`）
- 三处统一走 `handleSyncMainSites`（已在 App.vue `useAppActions` 暴露）

**涉及文件**：上述三个前端文件

---

### SYNC-006：定时检测串行 vs 主站同步并行，策略不统一

**位置**：`backend/workers/scheduler.py:34-64`

**问题描述**：

主站同步用了三级并行，但 `run_scheduler_tick` 里站点检测是 `for site in due_sites: detect_site(...)` 逐个串行。

**待评估**：
- 是否有意设计（避免检测并发打上游被限流）？
- 到期站点多时是否构成瓶颈？

**涉及文件**：`backend/workers/scheduler.py`

---

### SYNC-007：`find_monitor_site_for_channel` 在 sync_service import 但主流程未用

**位置**：`backend/services/sync_service.py:47`

**问题描述**：

`find_monitor_site_for_channel` 被 import 到 sync_service，但主站同步的候选导入与 link 对账流程并未使用它（它只在渠道 key 匹配 `channel_match_service.py:386` 和平台检测 `platform_detect_service.py:89` 中使用）。

**待确认**：
- sync_service 中是否有隐藏调用？
- 若无用可移除该 import

**涉及文件**：`backend/services/sync_service.py`

---

## 附录：冗余代码清单

| 冗余项 | 位置 | 状态 |
|--------|------|------|
| `SessionSyncService` 类 | `session_sync_service.py:984-989` | 已删除（2026-09-03） |
| `DiscoveryService` 类 | `discovery_service.py:573-578` | 已删除（2026-09-03） |
| `AdminSiteService` 类 | `admin_site_service.py:558-568` | 已删除（2026-09-03） |
| `setPlatform` if/else 双分支 | 见 AUTH-004（跨模块） | 已修复（2026-09-03） |
| 前端三处同步入口重复展示 | SYNC-005 | 部分解决（见下） |

---

## 处理记录（2026-09-03，分支 `audit-cleanup-fixes`）

- **SYNC-001/003**：normalize 线程池已移除，改为串行调用（GIL 下纯 CPU
  并行无收益）。HTTP 拉取池**保留**：两个独立 HTTP 请求是真实 I/O 并行，
  每主站 2 线程的短生命周期池创建开销相对请求耗时可忽略，且池数量受
  顶层并行度约束；已在代码注释中说明保留理由。若后续仍要压线程数，
  可考虑顶层统一池，属独立重构。
- **SYNC-004**：三个类全仓 grep（backend/ tests/ app.py，含非实例化引用）
  确认 0 引用后删除。
- **SYNC-005**：**部分过时**。`5668bae`/`dd47586` 工具栏精简后，ChannelsPage
  已改走 `useAppActions` 的 `handleSyncMainSites`（不再直连
  `api.syncMainSites`）。剩余两处：App.vue（共享实现本体）与
  SitesPage.vue `syncAllFromMain`。后者保留是因为它输出逐主站的
  渠道/分组明细与失败详情面板，比共享 toast 信息更丰富；统一会降级
  UX，待产品决策（共享 action 扩展明细输出 vs SitesPage 放弃明细）。
- **SYNC-007**：死 import 已移除。
- **SYNC-002 / SYNC-006**：维持待确认/待评估，需产品决策（是否上 SSE、
  检测串行是否为限流的有意设计）。
