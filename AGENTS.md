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
| 后端 | `backend/`（FastAPI + Uvicorn；数据访问直连 **MySQL**，驱动 PyMySQL，连接池复用 SQLAlchemy Engine）+ 根目录 `app.py`（兼容启动入口） |
| 前端 | `apps/web`（Vue 3 + Vite 7 + Tailwind 4 + vue-router，priceai-ui 风格；不再使用 React） |
| 数据 | **MySQL**（`DB_*` 走 `.env`，运行期数据全部在 MySQL） |
| 部署 | `python3 app.py` / Docker Compose（自带 `mysql:8.4`） |

---

## 强制规则

1. 对用户默认**简体中文**。
2. UI 必须遵循 skill **`priceai-ui`**（`.agents/skills/priceai-ui/SKILL.md`）。
3. **兼容现有 MySQL 数据**，禁止未确认清空用户数据。
4. 密钥（DB 密码、token、webhook、cookie、refresh_token）只放本地 `.env` 或 MySQL 表，勿写入源码 / git / 对话明文；UI 展示一律走 `mask_channel_key` / `mask_newapi_user_token_key`。
5. 推送通道：邮件 + 企业微信；不主动复活 QQ 推送。
6. **优先用成熟三方库，不手写基础设施**：凡是成熟库已解决、且与单进程架构匹配的通用问题（连接池 / 定时调度 / .env 解析 / HTTP 客户端 / TTL 缓存等），一律用现成包，不要自己造轮子。后端依赖白名单：
    - `fastapi` + `uvicorn`（Web / ASGI）
    - `PyMySQL`（驱动）+ `SQLAlchemy`（**仅**用 Engine/QueuePool 做连接池与生命周期管理，禁用 ORM 模型 / declarative / 异步引擎）
    - `APScheduler`（进程内定时调度，BackgroundScheduler）
    - `python-dotenv`（.env 加载）
    - `httpx`（上游 HTTP 客户端，共享 Client + 手动重定向）
    - `curl_cffi`（TLS 指纹兼容回退传输，仅限连接被上游重置时使用；禁再走 subprocess curl）
    - `cachetools`（TTLCache 作进程内缓存容器，锁仍由 core.state 提供）
    - 其余场景优先标准库（smtplib / urllib.parse / zoneinfo 等）；新增任何依赖必须在 AGENTS.md 白名单登记并说明理由。
7. **后端必须按 FastAPI 格式操作**：APIRouter + Depends + Pydantic v2 schema + 异常处理器 + lifespan；不再新增 stdlib `BaseHTTPRequestHandler` 风格的处理函数。
8. 改 API 契约时**同时**改 `backend/api/routers/*.py`、`backend/api/schemas/*.py` 与 `apps/web/src/lib/api/<domain>.ts` 与对应页面。
9. **默认不新增测试代码或测试文件**；只有用户明确要求时才补充测试。
10. **两类站点不可混淆**（详见 `docs/product.md`；补充记忆 `upstream-two-site-model`）：
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
│   │   │                        #   settings / session_sync / admin_sites
│   │   └── schemas/             # Pydantic v2 请求/响应模型
│   ├── core/
│   │   ├── config.py            # Settings（HOST/PORT/ENABLE_API_DOCS/SCHEDULER_ENABLED/...）
│   │   ├── security.py          # require_console_auth 依赖 + Bearer 解析
│   │   ├── errors.py            # JSON 错误信封 + 全局异常处理
│   │   ├── state.py             # 进程级单例（DB_LOCK / STOP_EVENT / 缓存 / 锁）
│   │   ├── time.py              # APP_TIMEZONE / app_now / stable_hash
│   │   └── normalize.py         # 纯函数：URL/cookie/掩码/分类辅助
│   ├── services/                # 业务 Service 层
│   ├── repositories/            # 表级仓储（sites / admin_sites / changes / notifications）
│   ├── integrations/            # NewAPI / sub2api / SMTP / 企业微信 客户端门面
│   ├── workers/                 # SchedulerWorker（APScheduler）/ ModelCacheWorker
│   ├── db/                      # PyMySQL 连接池（SQLAlchemy Engine）/ 迁移 / schema 启动
│   └── legacy_runtime.py        # 已删除；勿再引用
├── apps/web/                    # 前端 Vue 3 控制台（见 apps/web/AGENTS.md）
│   ├── src/                     # 入口、路由、页面、组件、composables、api 客户端、类型、token
│   ├── vite.config.ts           # 别名 @ → src，/api 代理到 8000
│   └── package.json
├── docs/                        # 产品说明、浏览器同步剩余工作
├── design/                      # PriceAI 风格调研
├── tests/                       # 已有测试（默认不再新增）
├── .agents/skills/priceai-ui    # 前端 UI 规范 skill
├── AGENTS.md                    # 本文件
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## 后端规范 → 统一见 `backend/AGENTS.md`

分层职责、路由 / 鉴权 / Schema / 异常、数据库、Workers 的技术细节统一收敛在 **`backend/AGENTS.md`**（唯一权威版本，本文不再重复；两边冲突时以它为准）。全局只需记住：

- 唯一入口 `backend/main.py` 的 `app`；根目录 `app.py` 仅是兼容壳，生产命令 `uvicorn backend.main:app`。
- 调用链：`api/routers`（接口壳：声明路径 + 解析参数）→ `services`（业务实现）→ `repositories`（SQL）→ MySQL；第三方 HTTP 只走 `integrations/`。
- 同步阻塞 I/O（DB / HTTP / 文件）一律包 `run_in_threadpool`，别堵事件循环。

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

## 关键坑（先看这些再动手；以下正文自包含，以此为准）

- **NewAPI 上游**：`PUT` 渠道带 `status` 必失败；`/api/channel/batch` 端点是**删除**，别拿生产渠道试探。
- **主站同步** = 双向对账，消失的站点要**停用而非删除**；15s 自动 + 手动；只动 `discovery_links` 里有的。
- **浏览器同步**：终态原子化（先落库再返令牌）；`newapi_browser_request` 遇 401/403 先强刷会话再重试；`cache_key` 不含明文。
- **主站是 aiinfinite.online**：主站 `id=2` 是 `aiinfinite.online` / `manman`；任何测试 / 脚本严禁用 `main.example` 之类假数据覆盖真实主站（2026-08 事故教训）。
- **同步阻塞调用**：DB / urllib / 任何 IO 都包 `run_in_threadpool`，别把事件循环堵住。

> 背景与事故复盘可补充阅读记忆 `newapi-upstream-api-traps` / `newapi-main-site-sync-reconcile` / `newapi-session-sync-cas` / `main-site-aiinfinite-and-test-clobber-incident`；工具不支持记忆时忽略此行，以上面正文为准。

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

- 不要改成深色 Linear 风却声称 PriceAI
- 不要为「好看」删监控字段与检测能力
- **依赖纪律**：白名单外新增后端依赖必须先登记 AGENTS.md 并说明理由；禁止引入 Celery / RQ / Redis 队列、Django / Flask、Tortoise / 异步 ORM；SQLAlchemy 仅限连接池用途，禁写 ORM 模型
- 不要把密钥写进源码、git、对话明文；不要把 `.env` 提交
- 不要绕过 `require_console_auth` 直接挂新 router（用 `protected` 字典统一加依赖）
- 不要在 router 里手搓 dict 拼 JSON（用 Pydantic schema + 全局异常 handler）
- 不要新增 stdlib `BaseHTTPRequestHandler` 子类；新逻辑全走 FastAPI
- 不要在模块顶层 `start()` 任何线程；线程/调度统一在 `lifespan` 起停
- 不要引用已删除的 `legacy_runtime` / `compat`；新逻辑进 `services/` / `repositories/`
- 不要混淆 `sites`（监控）与 `admin_sites`（主站）；不要让监控站点的 token 驱动主站 CRUD
- 不要默认新增测试；用户没要就别写
- **前端不要写 React**（`.tsx` / `react-router-dom` / `lucide-react` / `react` / `react-dom` 依赖）；项目已迁 Vue 3，详见 `apps/web/AGENTS.md`
- 不要在 `apps/web/src/components/ui/` 引入 `composables/` / `lib/api/` / `lib/*` 业务工具；越界就下沉
- 不要在 `apps/web/src/lib/types.ts` 用 `any` 收口后端透传字段；用 `[key: string]: unknown`
