# FastAPI 后端真实拆分计划

更新时间：2026-08-20

## 目标

在保留现有 MySQL 数据、前端契约和全部监控功能的前提下，把当前
`app.py` / `backend/legacy_runtime.py` 的单体实现拆为可维护的 FastAPI 分层后端。

目标调用链：

```text
FastAPI Router
  -> Service
     -> Repository -> MySQL
     -> Integration Client -> NewAPI / sub2api
  -> DomainError -> 统一异常响应

Worker -> Service
app.py -> 启动入口和必要兼容导出
```

目录存在或某个类只是转发旧函数，不算完成。只有生产调用链不再回到
`legacy_runtime.py` 的对应实现，才算该职责已迁移。

## 当前基线

- FastAPI 路由已挂载，生产 HTTP 入口已不再使用旧 `BaseHTTPRequestHandler`。
- `backend/legacy_runtime.py` 仍约 11,700 行，但生产业务已按垂直链路迁入 `services`、`repositories`、`integrations` 和 `domain`；遗留文件只保留兼容导出与历史 Handler shim。
- Repository、连接池、DDL 与启动迁移已收口到 `backend/db`；旧运行时复用同一连接池仅供兼容入口使用。
- `app.py` 仍必须保持 `python3 app.py` 启动和现有迁移脚本兼容。
- 不清空、不迁移、不修改生产 MySQL 数据；验证只使用独立测试库。

## 当前完成度（2026-08-19）

按“生产调用链是否已经离开旧单体”统计，而不是按目录数量统计：

| 垂直链路 | 状态 |
| --- | --- |
| 控制台认证 | 已完成，使用 `core/console_auth.py` |
| 邮件 / 企业微信通知 | 已完成，使用 Integration + Repository |
| 主站渠道 HTTP 协议 | 已完成 CRUD、分页、分组、管理端 session、受保护 key 和用户 key 精确匹配 |
| 总览 / 站点列表 | 已完成，`MonitoringRepository` 直接读 MySQL |
| 站点 CRUD | 已完成，站点 CRUD、认证状态和缓存失效均由 Service/Repository 负责 |
| 应用设置 | 已完成，`SettingsRepository` 直接读写 MySQL |
| 分组差异计算 | 已完成纯函数迁移，旧入口仅兼容转发 |
| 发现导入 / 溯源 | 已完成，导入校验、站点复用和关联写入走 `DiscoveryService -> DiscoveryRepository` |
| 浏览器同步请求 | 已完成，请求生命周期、浏览器会话验证与凭据 CAS 写入均走 `SessionSyncService -> Integration -> SessionSyncRepository` |
| 站点检测 / 快照写入 / 账户额度 | 已完成，手动/定时检测、分组读取、差异、快照、通知和账户额度使用同一 Service 链路 |
| 模型读取 / 缓存 | 已完成，缓存、NewAPI/sub2api pricing/perf/uptime、模型健康和主站 key 读取均已迁出 |
| 主站同步 / Worker | 已完成，主站同步、平台探测、快照对账、定时检查和 key 刷新均走新 Service |

因此生产 HTTP、Worker、Repository、Integration、DDL/启动链路均不再直接进入旧单体；普通监控站点的
NewAPI/sub2api 登录、refresh、浏览器/密码恢复和凭据 CAS，以及主站受保护 key、渠道 key 精确匹配和
2FA 后首个 key 刷新均已迁出。
`backend/legacy_runtime.py` 仍约 11,700 行，但它已不在生产 FastAPI/Worker 调用链中；
剩余工作是确认兼容使用方后删除历史导出，而不是继续把业务逻辑留在单体中。

## 不变约束

- 保留站点 CRUD、定时/手动检测、快照、分组倍率 diff、变化记录、邮件、企业微信、账户额度、模型健康、主站渠道管理、同步和发现。
- 后端不增加 ORM、任务队列或额外 Python 依赖；数据库驱动只使用 PyMySQL。
- 不写入或输出数据库密码、token、cookie、webhook、渠道 key。
- API 契约变化必须同步 `apps/web/src/lib/api.ts`、`apps/web/src/lib/types.ts` 和页面。
- 默认不新增测试文件；只运行现有检查和必要的手工接口验证。
- 不因为拆分而改变 NewAPI / sub2api 的认证、2FA、refresh、错误分类或脱敏语义。

## 批次 1：数据库边界和 Repository

状态：已完成（生产数据库边界已脱离兼容运行时）。

### 要做

1. `backend/db` 成为唯一的 Repository 数据库入口：连接租用、事务、占位符适配、查询和写入。
2. `sites`、`admin_sites`、`changes`、`notifications` 相关 SQL 全部落在对应 Repository。
3. 为 settings、session-sync、discovery、snapshot 补齐现有表的 Repository；不改变表结构。
4. Repository 不导入 `legacy_runtime.py`，不处理 HTTP、上游请求、平台判断或业务规则。
5. 删除、绑定、key cache 等多表写入在一个事务内完成。

### 完成标准

```text
backend/repositories/* 不再 import legacy_runtime
backend/repositories/* 不直接依赖 legacy 的 db_* / _q helper
backend/db 只保留数据库基础设施，不含业务规则
```

### 注意事项

- 空 token、密码、webhook 在编辑场景代表保留旧值，不能被更新逻辑误清空。
- 所有 Repository SQL 使用 PyMySQL 参数化，不能留下 SQLite `?` 占位符错误。
- 连接池本身可以先保持兼容，等所有 Repository 使用新边界后再从旧运行时迁出。

当前进度：`sites`、`admin_sites`、`changes`、`notifications`、`monitoring`、
`settings` Repository 已有真实 SQL 实现；站点 CRUD、总览、站点列表、变化/快照读取
和设置页已走新边界。`session_sync` 已覆盖请求创建、替换、查询、浏览器会话验证、凭据 CAS 写入和终态写入；
`discovery` 已覆盖幂等导入与 `site_discovery_links` 事务写入；快照/变化/站点状态由
`ChangeRepository.record_check()` 一次事务提交。`backend/db/pool.py` 现在独立持有
PyMySQL 连接池，旧运行时仅复用同一池；`schema.py`、`schema_ddl.py`、`migrations.py`
已负责生产 DDL、幂等补列、一次性数据迁移、启动等待和默认通知设置。

## 批次 2：上游 Integration Client

状态：已完成。

### 要做

1. 将 HTTP transport、请求响应解析和错误脱敏迁入 `backend/integrations`。
2. `NewApiClient` 负责管理员渠道、分组、渠道详情、key、2FA proof、浏览器 session 和分页。
3. `Sub2ApiClient` 负责登录、refresh、401 重试、管理员权限、渠道、分组、定价和渠道编辑。
4. Client 接收完整 site 上下文，不能退化成只传 base URL/token。
5. Client 不直接写业务表；认证状态持久化通过 Repository 由 Service 编排。

### 完成标准

```text
backend/integrations/* 不再把核心请求转发给 legacy_runtime
NewAPI 和 sub2api 的真实上游协议都由 Client 层实现
列表接口不会返回明文 token、密码、cookie 或渠道 key
```

### 注意事项

- NewAPI 的渠道 key 读取必须保留浏览器 session、2FA proof、限流与缓存逻辑。
- sub2api 必须保持 refresh token 轮换、401 重试、分页完整性检查和 400/404/429 分类。
- 上游网络 I/O 不得在数据库事务内执行。

当前进度：`transport.py`、`newapi_admin.py`、`sub2api_admin.py` 已实现标准库 HTTP
协议；NewAPI/sub2api 管理渠道的 list/detail/create/update/delete/batch、分页、分组、
登录、refresh 和 401 重试已由 Client 实际调用。监控用 NewAPI 分组、认证增强分组、
pricing 和 perf 标准 token 请求也已由 `integrations/newapi.py` 实际调用。普通 sub2api
监控端的用户登录、refresh 和读取协议已由 `integrations/sub2api.py` 实际调用，持久化
由 `Sub2ApiSiteAuthService -> SiteRepository` 编排；NewAPI 普通站点的浏览器/密码登录、
刷新和读取由 `NewApiSiteAuthService -> SiteRepository` 编排。NewAPI 用户 token 的分页、明文 key
精确比对与分组读取、sub2api 用户 key 的完整分页读取均已在 Integration 层实现；发现候选列表的本地站点状态
enrichment 已由 `DiscoveryRepository` 直接读取 MySQL；NewAPI 主站受保护 key 已迁到
`NewApiAdminSessionService -> NewApiProtectedKeyService -> AdminSiteRepository`，保留浏览器
session、2FA proof、限流、401 重试和 key cache。sub2api 主站管理端 session refresh 已迁到
`Sub2ApiAdminSessionService -> AdminSiteRepository`，保留 refresh token 轮换、过期判断、
401 重试和密码回退语义。

## 批次 3：Service 业务编排

状态：已完成。

### 要做

1. 重构 `AdminSiteService`：主站 CRUD、连接测试、渠道 CRUD、groups、mapping、match、key refresh。
2. 重构 `SiteService`：监控站点 CRUD、认证方式、密码登录、缓存失效。
3. 重构 `MonitoringService`、`ModelService`、`NotificationService`、`SettingsService`、`SessionSyncService`。
4. Service 只调用 Repository/Integration，负责业务校验、平台能力、事务编排和 DomainError。
5. Service 不返回 `JSONResponse` 或 `(payload, status)`，不写 SQL，不拼上游 URL。

### 完成标准

```text
backend/services/* 不直接执行 SQL
backend/services/* 不直接调用 legacy_runtime 的业务函数
所有业务错误统一抛出 DomainError
```

### 注意事项

- 主站 platform 不允许在编辑时切换。
- 修改 sub2api 凭据必须重新登录并原子更新保存的 session。
- 修改 NewAPI 登录凭据必须清理旧浏览器 session/proof。
- mapping/match 临时失败不能清空最后一次成功的 matched_groups。

当前进度：认证、通知、主站渠道 Service 已接入新边界；站点 CRUD 的 SQL 已移入
`SiteRepository`，模型/设置/总览读取部分已接入新 Repository。`SessionSyncService`
的请求生命周期、`NotificationService` 的变化通知编排、`ModelService` 的 pricing/perf
读取均已接入新边界。模型健康缓存、NewAPI uptime 和模型分组映射已由
`ModelCacheService -> Integration -> domain/model_health` 负责；sub2api 普通监控站点
的令牌被拒绝后 refresh/password/browser 回退已由 `Sub2ApiSiteAuthService` 接管；
NewAPI 普通站点的浏览器/密码登录、刷新和账户读取已由 `NewApiSiteAuthService` 接管。
`AdminSiteService` 的渠道匹配已拆到 `ChannelMatchService`：它只通过 Repository 和 Integration
完成认证来源选择、渠道 key 获取、NewAPI/sub2api 精确匹配、refresh CAS 和历史匹配保护；非空非法绑定 URL
会被拒绝，空 URL 可明确清除绑定。NewAPI key/session/proof 和 sub2api 主站管理 session 已由独立 Service 负责。

## 批次 4：监控、快照和差异领域

状态：已完成。

### 要做

1. 迁出 `detect_site`、账户额度读取、模型缓存、分组采集和健康数据。
2. 迁出快照写入、`diff_groups`、变化记录、模型上下架变化。
3. 将领域计算放到独立模块，将表访问放到 Repository。

### 完成标准

```text
监控检测、差异计算、快照和变化记录不再定义在 legacy_runtime.py
手动检测和定时检测调用同一 Service 入口
```

### 注意事项

- 保留模型 `model_added_to_group` / `model_removed_from_group` 变化类型。
- 单次上游失败不能覆盖最后一次成功快照或清空已有模型缓存。

当前进度：`/api/overview`、`/api/sites` 的读模型已由
`backend/repositories/monitoring.py` 独立实现；纯差异计算已迁到
`backend/domain/diff.py`，旧运行时只保留兼容转发；手动检测已切到
`CheckService -> ChangeRepository/SiteRepository`，快照、变化和站点状态更新在一个事务内。
NewAPI 的公开分组和认证增强分组已由 `NewApiClient` 采集，缓存模型名补充和邮件/企业微信
变化通知也已脱离旧检测函数；账户额度和普通监控站点认证回退已由新
Integration/Service/Repository 接管。定时 Worker 已调用 `CheckService`；主站同步和
受保护 key 刷新另由批次 5 的 Service 处理。

## 批次 5：同步、发现和 Worker

状态：已完成。

### 要做

1. 迁出主站同步、平台探测、discovery import、provenance reconcile。
2. 迁出 scheduler、key refresh、model cache worker，使 Worker 只调用 Service。
3. 保持 `admin_site_sync_state` 的完整快照安全边界。

### 完成标准

```text
backend/workers/* 不直接调用 legacy_runtime
同步、发现和对账逻辑由 Service/Repository/Integration 组合完成
```

### 注意事项

- 渠道和分组都成功后，才替换同步快照或执行 reconcile。
- 同步失败不得删除旧关联、旧快照或已导入站点。
- discovery import 必须幂等，重复导入不能创建重复站点。
- 平台探测失败不得把已知 sub2api 站点误写成 NewAPI。

当前进度：主站同步已由 `SyncService -> NewApiClient/Sub2ApiClient -> SyncRepository`
实现。渠道与分组必须完整读取成功后才进入事务；快照、导入站点、溯源链接对账、失效绑定/key
清理和 disable/delete reconcile 在同一事务内完成，失败只记录 `last_error`，不覆盖上次成功快照。
公开平台探测已迁到 `integrations/platform_probe.py`，探测失败不会把已知 sub2api 站点改写为
NewAPI。`/api/sites/discovery-import` 已由 `DiscoveryService` 校验请求并由
`DiscoveryRepository` 在单一事务内复用/创建监控站点、写入溯源关联；重复导入不会重复创建
站点或关联。`SchedulerWorker` 已通过 `CheckService` 执行到期检查，`KeySyncService` 负责
受保护 key 的分批刷新、限流/2FA 退避和调度状态；Worker 不再直接调用旧运行时。2FA proof
验证成功后会立即刷新一个渠道 key，失败会保留已通过验证的状态并返回可操作错误。受保护 key
的浏览器/2FA 请求已由 `NewApiAdminSessionService` 和 `NewApiProtectedKeyService` 接管；
sub2api 管理 session refresh 已由 `Sub2ApiAdminSessionService` 接管。

## 批次 6：Router、启动入口和收口

状态：实现完成，历史兼容导出待外部依赖确认后删除。

### 要做

1. Router 只做输入解析、Schema 序列化和 Service 调用。
2. 全部错误由 FastAPI exception handler 返回一致 JSON envelope。
3. `app.py` 只保留启动入口和已确认必要的兼容导出。
4. 每个旧 Handler shim 在对应 Service/HTTP 路径验证后删除。

### 完成标准

```text
backend/api 不导入 legacy_runtime 的业务函数
app.py 不承载生产业务 Handler、SQL 或上游协议实现
legacy_runtime.py 只保留临时兼容导出，且有明确删除清单
```

当前遗留只包括：`app.py` 对历史 `import app` 调用方的模块兼容替换，以及
`legacy_runtime.py` 中不再由生产 FastAPI/Worker 调用的 Handler shim 与兼容导出。删除这些前必须先确认
外部脚本和历史测试不再依赖它们，不能直接删除。

## 2026-08-20 收口记录

- 站点、模型、设置、通知、连接探测、账户额度和浏览器同步的 HTTP-facing Service
  已统一为普通返回值；参数、资源、平台能力和上游失败改由 `DomainError` 交给全局
  exception handler 生成 JSON 响应。
- 测试发送接口的配置错误、认证失败、数据库池繁忙和未捕获异常均有稳定的 JSON 错误边界，
  不再把 tuple 或裸异常交给 FastAPI。
- NewAPI 普通站点和主站的浏览器登录态状态只返回脱敏布尔值；浏览器 Cookie、token、密码、
  webhook 和渠道 key 不进入列表响应。
- `ModelCacheService` 内部仍使用状态码标记缓存刷新结果，供 Worker/缓存编排使用；这些状态码
  不直接暴露为 Router 的 HTTP tuple，属于内部缓存结果而非 API 契约。
- 旧 `app`/`legacy_runtime` 兼容导出仍保留，删除前需确认迁移脚本和历史外部调用方不再导入。

## 每批验证

每完成一个批次，至少检查：

```bash
python3 -m compileall -q backend app.py
python3 -c 'import backend.main'
```

在独立 MySQL 数据库中检查：

```text
GET /healthz
GET /api/overview
GET /api/sites
GET /api/admin/sites
```

涉及前端契约时再运行：

```bash
cd apps/web && npm run build
```

最终验收还需手工走通 NewAPI 主站、sub2api 主站、监控站点 CRUD、推送设置、同步发现和浏览器登录态；真实上游凭据不进入代码或文档。

## 停止条件

出现以下任一情况，先修复当前批次，不继续下一个批次：

- `python3 app.py` 或 `import backend.main` 失败。
- API 返回 HTML、裸异常或错误的 tuple 响应。
- 编辑动作清空已有密钥或登录态。
- NewAPI key refresh 丢失 session/proof，或 sub2api refresh 失效。
- 同步失败清理了旧数据。
- 验证环境连接到生产 `upstream` 数据库。
- 前端构建失败或 API 类型不匹配。
