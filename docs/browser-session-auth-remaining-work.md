# 浏览器登录态优先方案：当前剩余工作

> 更新时间：2026-08-02  
> 项目目录：`/Users/wang/Desktop/upstream`  
> 范围：仅统计“浏览器登录态优先，RT 刷新，账号密码兜底；没有登录态时提示提前登录”这条认证链路。

## 当前结论

sub2api 第一阶段主体已经完成，包括：

- 浏览器 AT 优先、RT 刷新、账号密码兜底。
- 只有明确的 401/403/令牌失效才进入下一层认证，网络错误和 5xx 不会误触发密码登录。
- Turnstile/2FA 无法后台完成时返回 `BROWSER_SESSION_REQUIRED`，由人工流程同步浏览器状态。
- 检测失败时保留原有分组和倍率，不会用空结果覆盖。
- AT、RT、Cookie、密码和内部认证上下文不会出现在普通 API 返回中。
- 已有 sub2api 站点的一次性无损迁移已实现。
- sub2api 旧同步请求写入 AT/RT 时已增加请求状态、平台、Origin 和认证模式的条件写入，旧请求不能覆盖新请求的凭据。

最近一次自动回归结果：

| 验证项 | 结果 |
|---|---:|
| Python 后端测试 | 196 通过 |
| Web Node 测试 | 56 通过 |
| Chrome 扩展测试 | 27 通过 |
| 当前已知失败测试 | 0 |

这些测试通过不代表下面列出的路径已经完成；目前缺少对应回归用例，因此仍需继续修复。

## 剩余工作统计

| 优先级 | 数量 | 含义 |
|---|---:|---|
| P0 | 3 | 可能造成旧登录态或旧状态覆盖新配置 |
| P1 | 3 | NewAPI 浏览器登录态没有贯穿全部业务接口 |
| P2 | 1 | 生产进程与真实 Chrome 端到端验收 |
| 合计 | 7 | 6 个代码工作包，1 个环境/验收工作包 |

预计还需补充至少 13 条自动回归测试和 4 项真实浏览器检查。

## P0：必须先完成

### 1. 同步请求终态与站点状态必须原子化

当前问题：

- `finish_session_sync_request()` 先更新同步请求，再单独更新 `sites` 或 `admin_sites`。
- 两次数据库写入之间如果创建了新同步请求，旧请求仍可能把新请求的 `pending` 状态改回 `ready`、`expired` 或 `failed`。
- 用户在验证过程中把站点改成 token 模式时，旧请求虽然不能再覆盖 sub2api AT/RT，但仍可能覆盖 `session_sync_status`。

需要修改：

- 用同一个进程锁和同一数据库事务完成“请求终态 + 目标状态”更新。
- 站点状态更新必须带上请求 ID、目标类型、平台、Origin 和当前 `auth_mode=browser` 条件。
- 如果条件不匹配，只结束旧请求，不修改当前站点状态。

验收标准：

- 请求 A 验证中创建请求 B，A 完成后 B 仍保持 `pending`。
- 请求 A 验证中将站点切换为 token，A 完成后站点仍保持 token 模式及其状态。

### 2. NewAPI 普通站点同步写入缺少 CAS 保护

当前问题：

- `persist_newapi_site_browser_session()` 仍按站点 ID 无条件写入。
- NewAPI 请求 A 已进入验证后，即使请求 B 已创建或用户已改成 token 模式，A 仍可能写回旧 AT、Cookie、Session ID 和用户 ID。

需要修改：

- 为 NewAPI 普通站点增加与 sub2api 相同的条件写入。
- 写入条件至少包含：请求仍为 `validating`、请求 ID 一致、站点仍为 `newapi + browser`、Origin 一致。
- 条件失败时返回“同步请求已失效”，不执行检测。

验收标准：

- 旧请求不能覆盖新请求。
- 旧请求不能把用户刚保存的系统令牌模式重新改回 browser。
- 条件失败时不调用 `detect_site()`。

### 3. NewAPI 管理站同步写入缺少 CAS 保护

当前问题：

- `_persist_admin_browser_auth()` 仍按管理站 ID 无条件写入浏览器 AT、RT Cookie 和 Session ID。
- 管理站请求 A 验证时创建请求 B，A 仍可能写入旧网页登录态。

需要修改：

- 管理站浏览器状态写入绑定当前同步请求 ID、管理站 ID、Origin、平台和 `validating` 状态。
- 保持系统访问令牌和 `security_proof` 完全不变。

验收标准：

- 管理站旧请求不能覆盖新请求。
- 浏览器同步永远不会覆盖 `admin_sites.access_token`、`access_user_id` 或 `security_proof`。

## P1：NewAPI 认证链路统一

### 4. 渠道倍率匹配没有完整复用 NewAPI 浏览器登录态

当前问题：

- `fetch_all_newapi_user_tokens()` 和 `fetch_newapi_user_token_key()` 使用 `newapi_auth_headers()`。
- browser 模式实际需要 `newapi_site_browser_auth_headers()` 中的 Bearer、`X-Auth-Session` 和 Cookie。
- 因此同 Base URL 的监控站点明明已经同步浏览器状态，渠道 key 对应分组仍可能读取失败，表现为倍率刷新不出来。

需要修改：

- 新增统一的“按站点请求 NewAPI 用户接口”执行器。
- token 模式使用系统访问令牌头；browser 模式先确保会话有效，再使用完整浏览器头。
- `/api/token/` 列表与 `/api/token/:id/key` 必须共用该执行器。

验收标准：

- browser 模式渠道匹配请求包含完整会话头。
- token 模式保持现有行为。
- 返回和日志中不出现任何真实 key、Cookie 或 Session ID。

### 5. 模型、pricing、uptime 和 perf-metrics 绕过 browser 认证

当前问题：

- `build_site_models_payload()` 直接调用旧的 `fetch_newapi_model_data()`。
- `fetch_newapi_model_data()`、`fetch_newapi_pricing()`、uptime 缓存、perf summary 和 perf detail 都使用普通 token 头。
- 页面上的模型、倍率或性能数据可能因此在 browser 模式下为空或报 401。

需要修改：

- 增加按完整站点记录工作的 NewAPI 请求入口，而不是只传 `base_url/access_token/user_id`。
- `/models`、`/pricing`、`/uptime/status`、`/perf-metrics/summary`、`/perf-metrics` 全部复用同一认证执行器。
- uptime 缓存键不能包含明文 token、Cookie 或 Session ID；只使用稳定且脱敏的站点标识。

验收标准：

- browser 模式的上述接口都使用 Bearer、`New-Api-User`、`X-Auth-Session` 和 Cookie。
- token 模式接口结果不回归。
- 模型与倍率刷新失败时保留上一次成功数据并展示真实原因。

### 6. NewAPI 收到提前 401/403 时不会强制刷新并重试

当前问题：

- `ensure_newapi_site_browser_session()` 主要依赖本地过期时间判断。
- 如果服务端提前撤销 AT，或者本地过期时间尚未到但请求返回 401/403，账户、分组、token、模型和指标请求会直接失败。
- 管理站渠道 key 目前只有较窄的错误形态会触发刷新，裸 401、403 或其他明确认证失败可能无法恢复。

需要修改：

- 建立共享的 NewAPI browser 请求执行器：正常请求一次，明确 401/403 时 `force=True` 刷新，再重试一次。
- 最多重试一次，禁止循环。
- 网络错误、超时、429 和 5xx 不触发刷新。
- 刷新失败时返回脱敏、可操作的“请重新网页登录/同步”提示。

验收标准：

- 本地过期时间未到但首次返回 401 时，可以刷新并成功重试。
- 403 认证失效按相同规则处理。
- 503、网络错误和限流不会触发 RT 或密码兜底。

## P2：部署和真实浏览器验收

### 7. 生产进程与 Chrome 真实流程尚未完成最终验收

当前状态：

- 自动测试已通过。
- 最近检查时 `127.0.0.1:5173` 没有运行中的 Upstream 服务，`/api/overview` 和 `/api/sites` 无法完成在线验证。
- 因服务未运行，修复后的真实 Chrome 同步、重新检测和倍率刷新还没有完成最后一轮验收。

需要完成：

1. 在 `/Users/wang/Desktop/upstream` 重新构建前端并启动 `python3 app.py`。
2. 确认实际监听端口，验证 `/api/overview`、`/api/sites` 返回 200。
3. 在 Chrome 保持目标上游已登录，并重新加载本地同步扩展。
4. 对一个 sub2api 站点执行“重新同步 → 手动检测 → 刷新倍率”。
5. 对一个 NewAPI 普通站点执行相同流程。
6. 对一个 NewAPI 管理站验证浏览器同步不改变系统令牌和 2FA proof。
7. 只检查 `has_access_token`、`has_refresh_token`、同步状态、分组数量和时间戳，不打印真实凭据。

## 建议实施顺序

1. 修复公共同步终态原子性。
2. 完成 NewAPI 普通站点和管理站 CAS 写入。
3. 建立 NewAPI 共享 browser 请求执行器和一次刷新重试。
4. 接入渠道匹配、模型、pricing、uptime 和 perf-metrics。
5. 补齐自动回归测试。
6. 构建、重启并完成 Chrome 真实验收。

## 需要新增的回归测试

至少补充以下 13 条：

1. 旧同步完成不能覆盖新请求的 `pending` 状态。
2. 站点从 browser 改为 token 后，旧同步不能修改状态。
3. NewAPI 普通站点旧同步不能覆盖新同步。
4. NewAPI 普通站点旧同步不能覆盖人工 token 编辑。
5. NewAPI 管理站旧同步不能覆盖新同步。
6. browser 模式 `/api/token/` 使用完整浏览器头。
7. browser 模式 `/api/token/:id/key` 使用完整浏览器头。
8. browser 模式 pricing/uptime 使用完整浏览器头。
9. browser 模式 perf summary/detail 使用完整浏览器头。
10. browser 模式账户接口提前 401 后刷新并重试一次。
11. browser 模式分组接口提前 403 后刷新并重试一次。
12. token/key 接口提前 401 后刷新并重试一次。
13. 网络错误、429、5xx 不触发刷新或密码登录。

## 完成定义

以下条件全部满足后，这一认证改造才算完整完成：

- 7 个剩余工作包全部关闭。
- 新增回归测试全部通过。
- 完整 Python、Web、扩展测试仍保持零失败。
- 前端生产构建成功。
- 两个基础 API 返回 200。
- sub2api、NewAPI 普通站点、NewAPI 管理站三条 Chrome 流程均通过。
- 全过程不输出、不记录、不回传真实 AT、RT、Cookie、密码、渠道 key 或同步 secret。
