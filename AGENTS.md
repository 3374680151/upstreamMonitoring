# Upstream — Agent 全局规则

> 路径：当前仓库根目录
> 后端以 FastAPI 为准（`backend/main.py`），前端以 Vue 3 + Vite 控制台（`apps/web`）为准，UI 风格为 PriceAI。

---

## 项目定位

**中转站管理系统 / 上游倍率监控面板**（双角色并存）：

- 通过 NewAPI / sub2api 官方 API 管理主站渠道（增删改/启停切换/权重优先级/测试/批量/平衡）
- 监控 NewAPI / sub2api 上游站点的分组、倍率、描述、模型上下架等变化
- 定时采集 + diff + 变化记录 + 邮件/企业微信推送
- 监控之上再加控制台登录鉴权、MySQL 后端、Docker 一键部署、开源脱敏

| 层 | 实现 |
|----|------|
| 后端 | `backend/`（FastAPI + Uvicorn；数据访问直连 **MySQL**，驱动 PyMySQL）+ 根目录 `app.py`（兼容启动入口） |
| 前端 | `apps/web`（Vue 3 + Vite 7 + Tailwind 4 + vue-router，priceai-ui 风格；不再使用 React） |
| 数据 | **MySQL**（`DB_*` 走 `.env`；运行期数据全部在 MySQL，SQLite 仅作迁移源） |
| 部署 | `python3 app.py` / Docker Compose（自带 `mysql:8.4`） |

---

## 强制规则

1. 对用户默认**简体中文**。
2. UI 必须遵循 skill **`priceai-ui`**（`.agents/skills/priceai-ui/SKILL.md`）。
3. **兼容现有 MySQL 数据**，禁止未确认清空用户数据；`data/` 里的旧 SQLite 与备份整体 gitignore。
4. 密钥（DB 密码、token、webhook、cookie、refresh_token）只放本地 `.env` 或 MySQL 表，勿写入源码 / git / 对话明文；UI 展示一律走 `mask_channel_key` / `mask_newapi_user_token_key`。
5. 推送通道：邮件 + 企业微信；不主动复活 QQ 推送。
6. 后端依赖收敛：**仅允许 PyMySQL + FastAPI + Uvicorn 三个第三方库**（数据库驱动 + ASGI 框架），其余走标准库；前端可在 `apps/web` 使用 npm 依赖。
7. **后端必须按 FastAPI 格式操作**：APIRouter + Depends + Pydantic v2 schema + 异常处理器 + lifespan；不再新增 stdlib `BaseHTTPRequestHandler` 风格的处理函数。
8. 改 API 契约时**同时**改 `backend/api/routers/*.py`、`backend/api/schemas/*.py` 与 `apps/web/src/lib/api/<domain>.ts` 与对应页面。
9. **默认不新增测试代码或测试文件**；只有用户明确要求时才补充测试。
10. **两类站点不可混淆**（详见 `docs/product.md` 与记忆 `upstream-two-site-model`）：
    - `sites`（监控/渠道站点）= 只读盯梢上游分组/倍率/余额/历史；
    - `admin_sites`（主站/管理站点）= 登录自己的 NewAPI / sub2api 后台做完整渠道 CRUD。
    两者不共用令牌、不互相关联；删除主站只从本控制台移除，不影响上游后台。

---

## 仓库结构（真实结构）

```
upstream/
├── app.py                       # 兼容启动入口：内部启动 backend.main:app
├── backend/                     # FastAPI 应用本体（见 backend/AGENTS.md）
│   ├── main.py                  # FastAPI app + lifespan + 异常处理 + 路由挂载 + 静态文件
│   ├── api/
│   │   ├── routers/             # APIRouter 分域：auth / monitoring / notifications /
│   │   │                        #   settings / session_sync / admin_sites / compat
│   │   └── schemas/             # Pydantic v2 请求/响应模型
│   ├── core/
│   │   ├── config.py            # Settings（HOST/PORT/ENABLE_API_DOCS/SCHEDULER_ENABLED/...）
│   │   ├── security.py          # require_console_auth 依赖 + Bearer 解析
│   │   ├── errors.py            # JSON 错误信封 + 全局异常处理
│   │   ├── state.py             # 进程级单例（DB_LOCK / STOP_EVENT / 缓存 / 锁）
│   │   ├── time.py              # APP_TIMEZONE / app_now / stable_hash
│   │   └── normalize.py         # 纯函数：URL/cookie/掩码/分类辅助
│   ├── services/                # 业务 Service + legacy_adapter 兼容层
│   ├── repositories/            # 表级仓储（sites / admin_sites / changes / notifications）
│   ├── integrations/            # NewAPI / sub2api / SMTP / 企业微信 客户端门面
│   ├── workers/                 # SchedulerWorker / ModelCacheWorker
│   ├── db/                      # PyMySQL 连接 / 迁移 / schema 启动
│   └── legacy_runtime.py        # 兼容运行期（db_* 助手 / 业务函数 / HTTP Handler）
├── apps/web/                    # 前端 Vue 3 控制台（见 apps/web/AGENTS.md）
│   ├── src/                     # 入口、路由、页面、组件、composables、api 客户端、类型、token
│   ├── vite.config.ts           # 别名 @ → src，/api 代理到 8000
│   └── package.json
├── data/                        # 旧 SQLite 与迁移残留（gitignore）
├── docs/                        # 产品说明、浏览器同步剩余工作
├── design/                      # PriceAI 风格调研
├── scripts/migrate_sqlite_to_mysql.py
├── tests/                       # 已有测试（默认不再新增）
├── .agents/skills/priceai-ui    # 前端 UI 规范 skill
├── AGENTS.md                    # 本文件
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## 后端必须按 FastAPI 格式操作（核心规范）

### 应用入口与生命周期

- 唯一入口是 `backend/main.py` 的 `app = FastAPI(...)`；`app.py` 仅做兼容壳，`uvicorn backend.main:app` 才是生产命令。
- 进程级资源（DB 池、调度器、模型缓存预热、demo seed）放在 `lifespan` 上下文管理器里，`yield` 之前初始化、`finally` 释放；不要在模块顶层 `start()` 任何线程。
- 关闭文档：`ENABLE_API_DOCS=0`（默认）；仅在调试时打开 `/docs`。

### 路由（Router）

- 按业务域拆 router：`auth / monitoring / notifications / settings / session_sync / admin_sites`，加上兜底 `compat`。每个文件内 `router = APIRouter()`。
- 挂载点在 `main.py`：`app.include_router(<domain>.router, prefix="/api", **protected)`，其中 `protected = {"dependencies": [Depends(require_console_auth)]}`。
- **新加 endpoint 必须挂在对应域的 router 上**；只在无法归类时才用 `compat` 兜底（`/{path:path}`），并尽快迁回专属 router。

### 鉴权依赖

- `backend.core.security.require_console_auth` 是所有 `/api/*` 的统一守门员；公开白名单走 `is_public_api_path`（已包含 `/api/auth/{login,logout,status}` 与 session-sync 桥接 complete 端点）。
- `CONSOLE_PASSWORD` 留空 = 不启用（仅建议本地 127.0.0.1 / 内网）；对外暴露前**必须**设置。
- 前端 `lib/api/client.ts` 收到 401 → 清 token + 派发 `console-unauthorized` 事件；后端 `require_console_auth` 抛 `HTTPException(401, detail={success:False, message, code:"unauthorized"})` 与之对齐。

### Schema（Pydantic v2）

- 请求/响应模型在 `backend/api/schemas/`，对应业务域同名文件：`admin_site.py / auth.py / common.py / notification.py / site.py`。
- 透传上游字段用 `CompatibilityModel`（`model_config = ConfigDict(extra="allow")`）；强契约字段直接 `BaseModel` 字段。
- 响应统一信封：`{"success": bool, "message": str|None, "data": ...}`；失败时 `code` 可选；不要在 router 里手搓 dict 拼 JSON。

### 异常处理

- 三个 handler 已在 `main.py` 注册：`StarletteHTTPException` / `RequestValidationError` / `legacy.DatabasePoolTimeoutError`；新增自定义异常请先在 `core/errors.py` 加 handler。
- 业务校验失败优先 `raise HTTPException(status_code, detail={success:False, message, code?})`；让全局 handler 统一渲染。

### Service / Repository 分层

- `services/<domain>_service.py`：业务编排门面，对应 router 直接依赖；可注入 repository。
- `repositories/<table>.py`：表级读/写；走 `legacy.db_query_*` / `db_execute_*` 助手（统一 `?` 占位符 + 线程安全 `DB_LOCK`）。
- `integrations/`：NewAPI / sub2api / 邮件 / 企微 HTTP 客户端门面；新加第三方协议时单建文件，**不**在 router 内直接 `urllib`。
- 同步阻塞 I/O（PyMySQL、urllib）必须包 `run_in_threadpool`，别阻塞事件循环。

### Workers（后台线程）

- `SchedulerWorker` 跑检测循环；`ModelCacheWorker` 做模型缓存预热。
- 启动/停止都在 `lifespan`；停止靠 `core.state.STOP_EVENT`。
- **不要**新增 `BackgroundTasks` 长任务、长 sleep 协程或第三方任务队列（Celery / RQ / APScheduler 都不引入）。

### 数据库

- 唯一驱动 PyMySQL；唯一并发保护 `core.state.DB_LOCK`（`threading.RLock`）。
- SQL 写 `?` 占位符，统一由 `legacy._q()` 转 `%s`；不要在仓库代码里直接 `cursor.execute("... %s ...", ...)`。
- 新表 / ALTER 走 `backend/db/migrations.py`（`legacy.init_db()` 是当前实现），不要手建表。

### 兼容层（legacy_runtime）

- `backend/legacy_runtime.py` 是迁移期兼容层（11K 行），承载已验证的 NewAPI/sub2api 适配与业务函数。
- **新代码默认不要再向 legacy_runtime 追加**，能下沉到 `services/` / `repositories/` / `integrations/` 的逐步下沉。
- `compat` router 与 `services/legacy_adapter.py` 是把 `Request` 喂给 `BaseHTTPRequestHandler` 派生类的桥接，仅用于未专门化的旧端点。

---

## 配置文件

- `.env` 已被 `.gitignore` 排除；密钥绝不入库。
- 业务配置（站点、推送、SMTP、Webhook）写在 MySQL，由 Web UI 维护。
- `.env` 仅承载运行期默认值：DB 连接、HOST/PORT、APP_TIMEZONE、CONSOLE_PASSWORD、CONSOLE_SESSION_TTL、SCHEDULER_ENABLED、ENABLE_API_DOCS、SLOW_REQUEST_THRESHOLD_MS、DB_POOL_*、UPSTREAM_HTTP_TIMEOUT。
- Docker 下 `DB_USER` 保持 `root`（compose 内置 mysql 只建 root），自建非 root 用户见 `docker-compose.yml` 注释。

---

## UI（PriceAI）

- 页底 `#f9f9f9`，白卡片，品牌绿 `#45bf78`
- 标题可用宋体 `font-serif`；正文无衬线
- 状态胶囊 + KPI + 高密度表
- token：`apps/web/src/styles/tokens.css`
- 暗色：`html[data-theme=dark]`
- 任何 UI 调整先在浏览器里验证（dev 模式 + 至少一遍主流程），不要仅靠 typecheck 就声称完成

---

## 功能边界（必须保留）

1. 监控站点 CRUD（NewAPI / sub2api）+ 主站 CRUD（NewAPI / sub2api）**两类独立**
2. 定时检测 + 手动检测
3. 快照 + 分组/倍率 diff（含模型上下架 `model_added_to_group` / `model_removed_from_group`）
4. 变化列表
5. 邮件 + 企业微信（含测试发送）
6. 总览 KPI / 站点详情 / 分组倍率弹窗 / 模型健康 / **主站健康**
7. 账户额度：NewAPI `/api/user/self`、sub2api `/api/v1/auth/me`（站点详情页按需查询）
8. 控制台登录鉴权（`CONSOLE_PASSWORD` 留空关闭）
9. 浏览器同步扩展桥接（`/api/session-sync/...`）—— 严格终态机（`ready` / `no_session` / `expired` / `permission_required` / `extension_unavailable` / `failed`）

---

## 关键坑（先看这些再动手）

- **NewAPI 上游**：`PUT` 渠道带 `status` 必失败；`/api/channel/batch` 端点是**删除**别拿生产渠道试探；详见 `newapi-upstream-api-traps` 记忆。
- **主站同步** = 双向对账，消失的站点要**停用而非删除**；15s 自动 + 手动；只动 `discovery_links` 里有的；详见 `newapi-main-site-sync-reconcile` 记忆。
- **浏览器同步**：终态原子化（先落库再返令牌）、`newapi_browser_request` 401/403 强刷重试、`cache_key` 不含明文；详见 `newapi-session-sync-cas` 记忆。
- **主站是 aiinfinite.online**：主站 `id=2` 是 `aiinfinite.online` / `manman`；测试套件千万别用 `main.example` 覆盖（2026-08 事故复盘见 `main-site-aiinfinite-and-test-clobber-incident` 记忆）。
- **D 端 router 兜底**：`/api/{path:path}` 走 `compat` 才会进 `legacy_handler`；新接口必须挂在专属 router。
- **同步阻塞调用**：DB / urllib / 任何 IO 都包 `run_in_threadpool`，别把事件循环堵住。

---

## 本地验证

```bash
# 后端（FastAPI 模式，自动读 .env）
python3 app.py                                # 等价于 uvicorn backend.main:app
# 或：
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1

# 前端开发
cd apps/web && npm run dev                    # 5173 端口，/api 代理到 8000

# 生产 UI 构建后由后端托管
cd apps/web && npm run build && cd ../.. && python3 app.py
```

至少验证：`/healthz` 返回 `{"status":"ok"}`；`/api/auth/status` 可访问；`/api/overview`、`/api/sites` 在登录后正常返回；页面可添加/编辑/检测站点；推送页可保存配置。

---

## 不要做

- 不要丢掉用户 `data/app.db`（仅迁移源，整体 gitignore）
- 不要改成深色 Linear 风却声称 PriceAI
- 不要为「好看」删监控字段与检测能力
- 不要在未请求时引入 SQLAlchemy / Celery / Django / Flask / httpx / aiohttp / APScheduler
- 不要把密钥写进源码、git、对话明文；不要把 `.env` 提交
- 不要绕过 `require_console_auth` 直接挂新 router（用 `protected` 字典统一加依赖）
- 不要在 router 里手搓 dict 拼 JSON（用 Pydantic schema + 全局异常 handler）
- 不要新增 stdlib `BaseHTTPRequestHandler` 子类；新逻辑全走 FastAPI
- 不要在模块顶层 `start()` 任何线程；线程/调度统一在 `lifespan` 起停
- 不要把 `legacy_runtime` 当默认目的地；新逻辑进 `services/` / `repositories/`
- 不要混淆 `sites`（监控）与 `admin_sites`（主站）；不要让监控站点的 token 驱动主站 CRUD
- 不要默认新增测试；用户没要就别写
- **前端不要写 React**（`.tsx` / `react-router-dom` / `lucide-react` / `react` / `react-dom` 依赖）；项目已迁 Vue 3，详见 `apps/web/AGENTS.md`
- 不要在 `apps/web/src/components/ui/` 引入 `composables/` / `lib/api/` / `lib/*` 业务工具；越界就下沉
- 不要在 `apps/web/src/lib/types.ts` 用 `any` 收口后端透传字段；用 `[key: string]: unknown`
