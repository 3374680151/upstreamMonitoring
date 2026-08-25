# backend/ — Agent 规范

> 范围：`backend/` 下所有 Python 模块（FastAPI 应用本体）。
> 上位规则见 `../AGENTS.md`，本文件只讲层与层之间的契约与禁止项。

---

## 目录与职责

| 路径 | 职责 | 写入新代码的判断 |
|------|------|------------------|
| `main.py` | FastAPI app 工厂、`lifespan`、异常处理、路由挂载、静态文件（根目录 `app.py` 仅是兼容壳，生产命令 `uvicorn backend.main:app`） | 只放装配逻辑；不放业务 |
| `api/routers/<domain>.py` | 一组相关 endpoint 的 APIRouter | **新 endpoint 默认先加这里** |
| `api/schemas/<domain>.py` | Pydantic v2 请求/响应模型 | 字段变更/新增都要改 |
| `core/config.py` | `Settings` dataclass + `.env` 解析（python-dotenv） | 加运行期变量请改这里 |
| `core/security.py` | `require_console_auth` 依赖 + Bearer token 解析 + 公开路径白名单 | 鉴权变动先改这里 |
| `core/errors.py` | JSON 错误信封 + 全局异常 handler（含 `DatabasePoolTimeoutError`） | 新增自定义异常先加 handler |
| `core/state.py` | **进程级单例的唯一来源**：锁、缓存（cachetools TTLCache）、`KeyedLockManager`、`STOP_EVENT` | 别在模块顶层自建 `Lock/RLock/Event` 或 dict 缓存 |
| `core/time.py` | 时区、ISO 解析、稳定哈希、调度间隔 | 业务时间处理走这里 |
| `core/normalize.py` | 纯函数：URL 规范化、cookie 解析、密钥掩码、字段过滤 | 不放 I/O、DB、模块级可变状态 |
| `services/<domain>_service.py` | 业务编排门面（`MonitoringService` / `NotificationService` / `AdminSiteService` / `SessionSyncService` / `SiteService` / `DiscoveryService` / `ChannelClassificationService`） | router 默认只依赖 service |
| `repositories/<table>.py` | 表级仓储：`sites` / `admin_sites` / `changes` / `notifications` | SQL 与表结构都在这里，service 通过它访问 DB |
| `integrations/<protocol>.py` | NewAPI / sub2api / 邮件 / 企微 HTTP 客户端门面 | 第三方协议单建文件 |
| `workers/scheduler.py` | `SchedulerWorker`：APScheduler BackgroundScheduler 封装 + `run_scheduler_tick` | 别的地方不直接 `start()` 线程 |
| `workers/cache.py` | `ModelCacheWorker` 模型缓存预热 | 同上 |
| `db/connection.py` | SQLAlchemy Engine（QueuePool+pre_ping）连接池 + `db_query_*`/`db_execute_*` 助手 | 业务不要直接 `import pymysql`，不要绕池直连 |
| `db/migrations.py` | 迁移入口（`init_db()`） | 表结构 / ALTER 走这里 |
| `db/schema.py` | schema 引导与初始化 | 别处不要 `CREATE TABLE` |

---

## 添加一个新 endpoint 的标准流程

1. **路由归属**：先把端点归到一个**现有域**（auth / monitoring / notifications / settings / session_sync / admin_sites）。
2. **路由声明**（`api/routers/<domain>.py`）：
   - 同步实现：`@router.get("/<path>")`、`@router.post(...)`，函数体直接返回 dict/Pydantic 模型。
   - 需要鉴权：**已经统一通过 `protected` 字典挂上了**，新 endpoint 不需要再加 `Depends`。
3. **业务逻辑**放到 `services/<domain>_service.py` 的同名方法；router 只做参数解析与序列化。
4. **表访问** 走 `repositories/<table>.py`，不要在 service 里直接 `db_query_*`。
5. **第三方协议** 走 `integrations/<protocol>.py`，不要在 service/router 里 `urllib`。
6. **请求/响应字段** 写 `api/schemas/<domain>.py` 的 Pydantic v2 模型；强契约用字段约束，透传上游字段继承 `CompatibilityModel`。
7. **错误处理**：业务校验失败 `raise HTTPException(status_code, detail={"success": False, "message": ..., "code": ...})`；数据库连接池忙 / 自定义异常由 `core/errors.py` 渲染。
8. **同步阻塞 I/O**：包 `from starlette.concurrency import run_in_threadpool`；DB 查询、urllib、文件 I/O 都跑在线程池。
9. **前端契约** 同步改 `apps/web/src/lib/api/<domain>.ts` + 对应页面。

> **已知遗留例外（勿模仿）**：`api/routers/monitoring.py` 的 `sync_sites`（约 242-396 行）与 `discovery_import` 是历史遗留的胖 handler，业务逻辑直接写在 router 里，属已知技术债。新代码一律按上述流程把业务下沉到 `services/`，禁止照抄此模式；重构触及时优先拆薄。

---

## FastAPI 规范细节

- 所有路由函数同步实现（`def`），把 I/O 用 `run_in_threadpool` 派发到线程；只有真正需要 `await` 的地方用 `async def`。
- `APIRouter()` 局部变量名固定 `router`；挂载时统一 `app.include_router(<domain>.router, prefix="/api", **protected)`。
- 路径参数：int 用 `{site_id}`；不要新增兜底 `{path:path}` 路由。
- `Query` / `Body` / `Path` 标注类型，不要裸 `request: Request`；请求体强契约用 Pydantic，弱契约用 `dict`。
- 公开路径白名单集中在 `core/security.is_public_api_path`（`/api/auth/{login,logout,status}` + session-sync complete 正则）。
- 鉴权依赖 `require_console_auth` 已经挂在 `protected` 上；新增 router 同样用 `protected` 字典挂载，不要单独 `Depends`。
- 401 契约：`require_console_auth` 抛 `HTTPException(401, detail={"success": False, "message": ..., "code": "unauthorized"})`；前端 `apps/web/src/lib/api/client.ts` 收到 401 清 token 并派发 `console-unauthorized` 事件——改鉴权时两端必须同步。
- `CONSOLE_PASSWORD` 留空 = 不启用鉴权（仅建议本地 127.0.0.1 / 内网）；对外暴露前**必须**设置。
- 响应统一信封 `{"success": bool, "data": ..., "message"?: str, "code"?: str}`；data 内容由 service 决定。
- 异常：
  - `HTTPException` → `http_exception_handler`（若 detail 是 dict 直接透传，否则包信封）。
  - `RequestValidationError` → 422 + `code: "validation_error"`，`details=exc.errors()`。
  - `DatabasePoolTimeoutError` → 503 + `code: "database_busy"`。
- 中间件：`request_timing_middleware` 已注册，写超过 `SLOW_REQUEST_THRESHOLD_MS` 自动脱敏打日志（method/path/status/elapsed_ms）。
- 静态文件：`SPAStaticFiles` 在 `apps/web/dist` 存在时挂到 `/`，SPA 路由找不到时回退 `index.html`。

---

## 状态、锁、缓存（强制）

- **唯一来源**：`core.state` 提供所有进程级 `Lock/RLock/Event`、缓存 dict、阈值常量。
- 禁止在业务模块顶层 `threading.Lock()` / `RLock()` / `Event()` / 进程级 dict；如需新加，**先在 `core/state.py` 注册并加注释说明用途**。
- 业务读写 DB 走 `DB_LOCK`；模型/可用率缓存走 `MODEL_CACHE_LOCK`；主站 channel key 读取走 `MAIN_CHANNEL_KEY_REQUEST_LOCK` + 限流冷却。
- 调度退出统一用 `STOP_EVENT.set()`；`SchedulerWorker.stop` 再 `shutdown(wait=False)`（在跑的一轮会在站点间隙自行退出）。
- 浏览器会话锁分三套命名空间：`ADMIN_BROWSER_SESSION_LOCKS` / `NEWAPI_SITE_BROWSER_SESSION_LOCKS` / `ADMIN_SUB2API_SESSION_LOCKS`，别混用。

---

## 数据库

- 驱动唯一 **PyMySQL**；连接池唯一来源 `db/connection.py` 的 SQLAlchemy Engine（QueuePool + `pool_pre_ping`），**仅用池化与生命周期管理**，禁用 ORM 模型 / declarative / 异步引擎。
- SQL 占位符统一 `?`（`_q()` 转 `%s`）；不要在仓库代码里直接写 `%s`。
- 连接/读/写超时由 `DB_CONNECT_TIMEOUT` / `DB_READ_TIMEOUT` / `DB_WRITE_TIMEOUT` 控。
- 助手函数（`db/connection.py`）：
  - `db_query_all(sql, params)` / `db_query_one(sql, params)` / `db_execute(sql, params)` / `db_execute_rowcount(sql, params)`
  - 事务：`db_connection()` context manager
- 池忙时抛 `DatabasePoolTimeoutError`，由 `core/errors.py` 渲染成 503 + `database_busy`。
- `DB_POOL_SIZE` 默认 8，最小 1、最大 32（`core/config.py`）；业务不要绕池直连。
- 表结构变更：`backend/db/migrations.py`（`init_db()`），别处不要 `CREATE TABLE` / `ALTER`。

---

## 集成（integrations/）

- 客户端门面：`NewApiClient` / `Sub2ApiClient` / `email.send` / `wecom.send`；HTTP 传输层在 `integrations/http.py`——基于 **httpx** 共享 Client（trust_env=False + 手动同源重定向控制），连接被上游重置时回退 **curl_cffi** 兼容传输；对外契约保持 `request_json / admin_request_json / request_json_with_headers`。
- 推送测试：邮件 `POST /api/notifications/test-email`、企微 `POST /api/notifications/test-wecom`。
- 浏览器桥接：仅 `session_sync` router；终态集合固定在 `core.state.SESSION_SYNC_TERMINAL_STATUSES` 与 `SESSION_SYNC_PAGE_FAILURES`。

---

## Workers

- `SchedulerWorker`：基于 **APScheduler BackgroundScheduler**（`max_instances=1` + `coalesce=True`），周期触发 `run_scheduler_tick`；在 `lifespan` 启动，受 `SCHEDULER_ENABLED` 控制；停止先 `STOP_EVENT.set()` 再 `shutdown(wait=False)`。
- 进程级资源（DB 池、调度器、模型缓存预热、demo seed）统一放 `lifespan`：`yield` 之前初始化、`finally` 释放；不要在模块顶层 `start()` 任何线程。
- `ModelCacheWorker.warm()`：进程启动后跑一遍模型缓存预热（不阻塞 HTTP 启动路径）。
- 业务内不直接 `start()` 线程；新增周期任务优先挂到现有 scheduler 讨论，不引入 Celery / RQ / Redis 队列。

---

## 配置（`core/config.py`）

- `Settings` dataclass 字段：host / port / enable_api_docs / scheduler_enabled / slow_request_threshold_ms。
- `.env` 加载用 **python-dotenv**（`load_dotenv(..., override=False)`），已存在的环境变量优先。
- 文档开关：`ENABLE_API_DOCS=0`（默认关闭 `/docs` `/redoc` `/openapi.json`）；仅调试时置 1。
- 新增运行期变量请：
  1. 在 `Settings` 加字段并提供 `os.getenv(..., default)` 解析；
  2. 必要时加 `_env_int` / `_env_bool` 辅助；
  3. 在 `.env.example` 与 `README.md` 的配置表里同步说明。

---

## 错误与日志

- 错误统一 JSON 信封（`{"success": False, "message": ..., "code"?: ...}`）。
- 慢请求日志：超过阈值在 `main.py` 的 `request_timing_middleware` 里 `print(..., flush=True)`（method/path/status/elapsed_ms，自动去 query string）。
- 业务 `print` 调试需带上下文（站点 id / 管理站 id / 异常堆栈）。
- 推送日志写入 `notification_logs` 表；测试发送也算一条日志。

---

## 安全 / 密钥

- 任何含 `access_token` / `password` / `cookie` / `secret` / `key` / `refresh_token` / `security_proof` 的字段：
  - 不进 channel metadata 快照（`core.normalize._sync_safe_value` 已在守护）；
  - UI 展示用 `mask_channel_key` / `mask_newapi_user_token_key`；
  - 错误信息不返回原始密钥。
- URL 规范化走 `core.normalize.normalize_base_url` / `_admin_site_origin` / `site_origin`；不接受带 `userinfo` 的 URL。

---

## 必读记忆（与本目录强相关）

> 记忆仅作背景补充阅读；关键事实已内联在上文各节与根 `AGENTS.md` 的「关键坑」一节，记忆工具不可用时以正文为准。

- [[upstream-goal-and-architecture]] — 中转站定位、SQLite→MySQL 由来
- [[newapi-upstream-api-traps]] — `PUT` 必失败 / batch 端点是删除
- [[newapi-session-sync-cas]] — 浏览器同步终态原子化、CAS 写回
- [[newapi-main-site-sync-reconcile]] — 主站双向对账、停用而非删除
- [[main-site-aiinfinite-and-test-clobber-incident]] — 主站 id=2 是 `aiinfinite.online`，测试别覆盖
- [[upstream-two-site-model]] — 监控站与主站是两类独立实体
