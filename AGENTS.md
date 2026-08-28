# Upstream — Agent 全局规则（唯一权威）

> 路径：当前仓库根目录
> 后端以 FastAPI 为准（`backend/main.py`），前端以 Vue 3 + Vite 控制台（`apps/web`）为准，UI 风格为「暖纸 + 浓墨」（见下文 UI 章节）。
> **本文件是全仓库唯一权威规范**：后端、前端规则全部在本文；原 `backend/AGENTS.md`、`apps/web/AGENTS.md` 已合并进本文并删除，引用它们的旧文档一律以本文为准。

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
| 前端 | `apps/web`（Vue 3 + Vite 7 + Tailwind 4 + vue-router，暖纸 + 浓墨风格；不再使用 React） |
| 数据 | **MySQL**（`DB_*` 走 `.env`，运行期数据全部在 MySQL） |
| 部署 | `python3 app.py` / Docker Compose（自带 `mysql:8.4`） |

---

## 强制规则

1. 对用户默认**简体中文**。
2. UI 以本文「UI（暖纸 + 浓墨）」章节 + `apps/web/src/styles/tokens.css` 为唯一权威；改 UI 前先读 `tokens.css` 注释。
3. **兼容现有 MySQL 数据**，禁止未确认清空用户数据。
4. 密钥（DB 密码、token、webhook、cookie、refresh_token）只放本地 `.env` 或 MySQL 表，勿写入源码 / git / 对话明文；UI 展示一律走 `mask_channel_key` / `mask_newapi_user_token_key`。
5. 推送通道：邮件 + 企业微信；不主动复活 QQ 推送。
6. **依赖纪律：基础设施用成熟库，业务逻辑优先标准库**。凡是成熟库已解决的通用基础设施问题（连接池 / 定时调度 / .env 解析 / HTTP 客户端 / TTL 缓存等）一律用现成包，不要自己造轮子；业务逻辑优先用标准库（smtplib / urllib.parse / zoneinfo 等），不要为小事引包。后端依赖白名单：
    - `fastapi` + `uvicorn`（Web / ASGI）
    - `PyMySQL`（驱动）+ `SQLAlchemy`（**仅**用 Engine/QueuePool 做连接池与生命周期管理，禁用 ORM 模型 / declarative / 异步引擎）
    - `APScheduler`（进程内定时调度，BackgroundScheduler）
    - `python-dotenv`（.env 加载）
    - `httpx`（上游 HTTP 客户端，共享 Client + 手动重定向）
    - `curl_cffi`（TLS 指纹兼容回退传输，仅限连接被上游重置时使用；禁再走 subprocess curl）
    - `cachetools`（TTLCache 作进程内缓存容器，锁仍由 core.state 提供）
    - 白名单外新增任何后端依赖，必须先在本文登记并说明理由。
7. **后端必须按 FastAPI 格式操作**：APIRouter + Depends + Pydantic v2 schema + 异常处理器 + lifespan；不再新增 stdlib `BaseHTTPRequestHandler` 风格的处理函数。
8. 改 API 契约时**同时**改 `backend/api/routers/*.py`、`backend/api/schemas/*.py` 与 `apps/web/src/lib/api/<domain>.ts` 与对应页面。
9. **默认不新增测试代码或测试文件**；只有用户明确要求时才补充测试。
10. **两类站点不可混淆**（详见 `docs/product.md`）：
    - `sites`（监控/渠道站点）= 只读盯梢上游分组/倍率/余额/历史；
    - `admin_sites`（主站/管理站点）= 登录自己的 NewAPI / sub2api 后台做完整渠道 CRUD。
    两者不共用令牌、不互相关联；删除主站只从本控制台移除，不影响上游后台。

---

## Git 分支工作流（强制，每个功能都要走）

**边界**：功能 / 修复等**代码变更**必须走分支；纯文档微调（本文件、README、docs 勘误）可直接在 master 提交。

1. **从 master 新建分支**：开始任何一个功能 / 修复前，先基于本地 `master` 最新状态新建分支，禁止直接在 `master` 上开发提交：
    ```bash
    git checkout master
    git pull --ff-only          # 若远程不可达或无远程跟踪，可跳过，以本地 master 为准
    git checkout -b <业务名>     # 例：sub2api-session-self-heal、channel-auto-demotion
    ```
    分支名 = **纯业务名**，全小写 + 连字符；**不加 `feat/` / `fix/` / `chore/` 等类型前缀**——类型信息放在提交信息里（`feat:` / `fix:` / `docs:` / `chore:`），不放分支名。
2. **开发与提交都在功能分支上**，提交信息延续仓库惯例：`feat(scope): 中文描述` / `fix(scope): ...` / `docs: ...` / `chore: ...`。
3. **合并必须等用户验证**：功能完成后先交给用户验证（本地跑起来走一遍主流程）。用户明确确认「验证通过」之前，**不得**合入 master，也**不得**推送远程。
4. **用户验证通过后 → 合入本地 master**：
    ```bash
    git checkout master
    git merge --no-ff <业务名>
    git branch -d <业务名>
    ```
5. **合入后再推送远程**：`git push origin master`。功能分支本身默认不推远程（用户要求时才推）。
6. 一次只开一个功能分支；上个功能未验证合入前，不要叠开新分支。若验证不通过，回到功能分支修复后再次交给用户验证。

---

## 后端规范（FastAPI）

### 分层职责（新代码放哪）

| 路径 | 职责 / 写入判断 |
|------|------|
| `main.py` | app 装配 + `lifespan` + 异常处理 + 路由挂载 + 静态文件；**只放装配逻辑** |
| `api/routers/<domain>.py` | endpoint 壳；**新 endpoint 默认先加这里**，业务逻辑不写在 router |
| `api/schemas/<domain>.py` | Pydantic v2 请求/响应模型；字段变更/新增都改这里 |
| `core/config.py` | `Settings` + `.env` 解析；加运行期变量改这里 |
| `core/security.py` | `require_console_auth` + 公开路径白名单 `is_public_api_path` |
| `core/errors.py` | JSON 错误信封 + 全局异常 handler；新增自定义异常先加 handler |
| `core/state.py` | **进程级单例唯一来源**：锁、缓存（TTLCache）、`KeyedLockManager`、`STOP_EVENT`；禁止业务模块顶层自建 `Lock/RLock/Event`/dict 缓存 |
| `core/time.py` | 时区、ISO 解析、稳定哈希；业务时间处理走这里 |
| `core/normalize.py` | 纯函数（URL 规范化 / cookie 解析 / 掩码 / 字段过滤）；不放 I/O、DB、可变状态 |
| `services/<domain>_service.py` | 业务门面（Monitoring / Notification / AdminSite / SessionSync / Site / Discovery / ChannelClassification）；router 默认只依赖 service |
| `repositories/<table>.py` | 表级 SQL（sites / admin_sites / changes / notifications）；service 不直接 `db_query_*` |
| `integrations/<protocol>.py` | 第三方协议客户端（NewAPI / sub2api / 邮件 / 企微）；service / router 不许 urllib 直连 |
| `workers/` | `SchedulerWorker` / `ModelCacheWorker`；别处不直接 `start()` 线程 |
| `db/connection.py` | 连接池 + `db_query_*` / `db_execute_*` 助手；业务别直接 `import pymysql`、别绕池直连 |
| `db/migrations.py` | `init_db()`；表结构 / ALTER 只改这里，别处不 `CREATE TABLE` |

### 新增 endpoint 标准流程

归到现有域（auth / monitoring / notifications / settings / session_sync / admin_sites）→ 在 `api/routers/<domain>.py` 声明路由（鉴权已由 `protected` 字典统一挂上，**无需再加 `Depends`**）→ 业务下沉 `services/<domain>_service.py` 同名方法 → 表访问走 repository → 第三方协议走 integrations → 请求/响应写 schemas（透传上游字段继承 `CompatibilityModel`）→ 校验失败 `raise HTTPException(status, detail={"success": False, "message": ..., "code": ...})` → 阻塞 I/O（DB / HTTP / 文件）包 `run_in_threadpool` → 前端同步改 `lib/api/<domain>.ts` + 页面。

> **已知技术债（勿模仿）**：`monitoring.py` 的 `sync_sites`（约 242-396 行）与 `discovery_import` 是历史胖 handler，业务直接写在 router 里。新代码一律按上述流程下沉到 `services/`，重构触及时优先拆薄。

### 路由、鉴权与错误契约

- 路由函数同步 `def` 实现 + `run_in_threadpool` 派发 I/O；`APIRouter()` 局部变量名固定 `router`；挂载统一 `app.include_router(<domain>.router, prefix="/api", **protected)`。
- int 路径参数写 `{site_id}`；禁止新增 `{path:path}` 兜底路由；不要裸 `request: Request`。
- 公开路径白名单只在 `core/security.is_public_api_path`（auth login/logout/status + session-sync complete 正则）。
- **401 契约**：detail 带 `code: "unauthorized"`；前端 `lib/api/client.ts` 收 401 清 token 并广播 `console-unauthorized` 事件——改鉴权时前后端必须同步。`CONSOLE_PASSWORD` 留空 = 不鉴权（仅限本机 / 内网；对外暴露必须设置）。
- 响应统一信封 `{"success": bool, "data": ..., "message"?, "code"?}`；`HTTPException` detail 是 dict 直接透传；`RequestValidationError` → 422 + `validation_error`；`DatabasePoolTimeoutError` → 503 + `database_busy`。
- 慢请求：`request_timing_middleware` 超 `SLOW_REQUEST_THRESHOLD_MS` 自动脱敏打日志（method/path/status/elapsed_ms）。
- 静态文件：`SPAStaticFiles` 在 `apps/web/dist` 存在时挂 `/`，SPA 路由回退 `index.html`。

### 状态、锁、缓存

- 锁 / 缓存只从 `core.state` 取：DB 读写 `DB_LOCK`；模型/可用率缓存 `MODEL_CACHE_LOCK`；主站 channel key 读取 `MAIN_CHANNEL_KEY_REQUEST_LOCK`（+ 限流冷却）；浏览器会话锁三套命名空间 `ADMIN_BROWSER_SESSION_LOCKS` / `NEWAPI_SITE_BROWSER_SESSION_LOCKS` / `ADMIN_SUB2API_SESSION_LOCKS` **不可混用**。需要新锁先在 `core/state.py` 注册并注释用途。
- 调度退出统一先 `STOP_EVENT.set()`，再 `shutdown(wait=False)`（在跑的一轮会在站点间隙自行退出）。

### 数据库

- 驱动唯一 PyMySQL；连接池唯一来源 `db/connection.py` 的 SQLAlchemy Engine（QueuePool + `pool_pre_ping`），**仅池化用途**，禁 ORM / declarative / 异步引擎 / 绕池直连。
- SQL 占位符统一 `?`（`_q()` 转 `%s`），不要在代码里直接写 `%s`；事务用 `db_connection()` context manager；池忙抛 `DatabasePoolTimeoutError`；`DB_POOL_SIZE` 默认 8（最小 1、最大 32）。
- 表结构变更只在 `backend/db/migrations.py` 的 `init_db()`。

### 集成与 Workers

- HTTP 传输层唯一在 `integrations/http.py`：httpx 共享 Client（`trust_env=False` + 手动同源重定向控制），连接被上游重置时回退 curl_cffi；对外契约保持 `request_json / admin_request_json / request_json_with_headers`。
- 浏览器桥接仅 `session_sync` router；终态集合固定在 `core.state.SESSION_SYNC_TERMINAL_STATUSES` 与 `SESSION_SYNC_PAGE_FAILURES`。
- Worker 基于 APScheduler BackgroundScheduler（`max_instances=1` + `coalesce=True`）；进程级资源（DB 池、调度器、缓存预热、demo seed）统一放 `lifespan`：`yield` 前初始化、`finally` 释放；`ModelCacheWorker.warm()` 不阻塞 HTTP 启动。新增周期任务挂现有 scheduler，不引入 Celery / RQ / Redis 队列。

### 配置、日志与安全

- 新增运行期变量三步：`Settings` 加字段（`os.getenv` + default）→ 必要时加 `_env_int` / `_env_bool` → `.env.example` 与 README 配置表同步说明。
- 业务 `print` 调试需带上下文（站点 id / 管理站 id / 异常堆栈）；推送日志写 `notification_logs` 表，测试发送也算一条。
- 含 `access_token` / `password` / `cookie` / `secret` / `key` / `refresh_token` / `security_proof` 的字段：不进 channel metadata 快照（`core.normalize._sync_safe_value` 守护）、错误信息不返回原始密钥、UI 展示走掩码函数。
- URL 一律走 `core.normalize` 规范化（`normalize_base_url` / `site_origin`），不接受带 `userinfo` 的 URL。

---

## 前端规范（Vue 3）

### 技术栈红线

- Vue 3.5 + `<script setup lang="ts">` + Composition API；**不写 React**（`.tsx` / 任何 `react*` 依赖）；**不引 Pinia / Vuex**——跨页共享用模块级 composable 单例（`useConsoleData`）或 provide/inject（`useAppActions`）。
- 图标只用 `lucide-vue-next`；npm + `package-lock.json` 入库。
- **vue-tsc 坑**：类型检查走 `vue-tsc --noEmit -p tsconfig.json`（`npm run build` 自带）；**禁止改回 `vue-tsc -b`**——build 模式在本机无限挂起，且 composite 会把 `vite.config.js` 编译进项目根，遮蔽 `vite.config.ts` 导致 dev server 丢 vue 插件。

### 路由与鉴权

- 路由表集中在 `src/router/index.ts`，全部懒加载，`/:pathMatch(.*)*` 重定向 `/`；**没有路由守卫**。
- **登录不是独立路由**：`LoginPage` 由 `App.vue` 按 `useAuth` 三态条件渲染；401 经 `console-unauthorized` 事件把 `authed` 打回 false 回到登录视图。新加路由同步更新 `AppShell.vue` 导航数组。

### 分层边界

- `components/ui/` 是公共原子件（统一从 `ui/index.ts` 出口 import，不直连单个 `.vue`、不建第二出口）：**禁止依赖 `composables/` / `lib/api/` / 任何 `lib/*`**，越界就下沉；ui 内不 fetch / 不 onMounted 拉数据；props / emits 强类型；弹窗族 `v-model:open` + `<Teleport to="body">`。
- 业务组件放 `components/` 根，**不自建 `components/business/`**；业务组件之间**不互相 import**（防环依赖），要复用抽 composable。
- composable（`useXxx.ts`）：内不许直接 fetch URL（统一走 `lib/api/<domain>`）；401 / 登出副作用只在 `useAuth`，其他 composable 不重复监听 `console-unauthorized`；定时器 / window 监听必须 `onUnmounted` / watch `onCleanup` 清理；状态默认 `shallowRef` / `ref<T[]>`，不要把响应体塞 `reactive`。
- 页面只做编排（拉数据 → 传业务组件 → 监听事件 → 调 api / composables），**禁止 `fetch('/api/...')`**；弹窗状态由页面/App 持有，跨层动作走 `useAppActions` 注入，不 props 层层透传。

### 数据层与工具函数

- `lib/api/` 按域拆分，每个文件对应后端一个 router（auth / monitoring / notifications / settings / sessionSync / adminSites）；新端点先后端后前端，并同步 `lib/types.ts` 与页面。
- `lib/api/client.ts` 是 Bearer token 唯一读写入口（`localStorage.console_token`）；401（非 auth 路径）清 token + 广播 `console-unauthorized`。页面只 `import { api } from '@/lib/api'`，不直连域文件、不在 `lib/api/` 之外建 `src/api/`。
- `lib/types.ts` 是唯一前端类型来源（不要复制到 `lib/api/types.ts`）；透传字段用 `[key: string]: unknown`，禁 `any`。
- 时间 / 金额 / 倍率 / 状态文案纯函数先搜 `lib/format.ts`（`fmtTime` / `platformLabel` / `ratioLabel` / `usd` / `truthy`）再写；金额格式化只用 `usd()`。

### 维护约定

- 样式只消费语义工具类（`bg-panel` / `text-ink-strong` / `border-line` …）与 colorTokens；不写死 hex、不 `bg-[var(--color-…)]`、不在 `ui/index.ts` 之外再 export 颜色表；改 UI 先读 `tokens.css` 注释。
- UI 改动必须在浏览器 dev 模式过一遍主流程（**浅色 + 暗色都要看**），不能只靠 typecheck 声称完成。
- 删除文件前先全局搜引用，确认 0 引用再 `rm`；不留「以防万一」的死代码和 `// removed` 注释，历史交给 git。
- `dist/` / `node_modules/` / `tsconfig.tsbuildinfo` 已 gitignore，不要提交。

---

## 命名与注释

> 原则：本节描述的是代码里**已经成立的惯例**，新代码照做；不要单方面改风格——批量翻译既有注释、重命名私有函数、无谓翻新旧类型写法都算。

### 后端（Python / FastAPI）

- 新模块顶部加 `from __future__ import annotations`（存量少数模块如 `db/migrations.py`、`workers/cache.py` 没有，不必回填）。
- 模块 docstring 用**英文**三引号：一行摘要 + 必要时补一段模块边界说明（参考 `core/normalize.py` 的写法）。
- 命名：函数 / 变量 `snake_case`；模块私有助手加 `_` 前缀（`_q` / `_sync_safe_value`）；常量 `UPPER_SNAKE_CASE`，进程级的只放 `core/state.py`。
- 文件按域命名：`services/<domain>_service.py`、`repositories/<table>.py`、`integrations/<protocol>.py`。
- 行为类方法动词开头：`create_ / delete_ / list_ / get_ / fetch_ / update_ / ensure_ / refresh_ / test_ / verify_ / build_`；返回派生数据的纯助手也接受名词短语（`admin_site_capabilities` / `channel_upstream_binding_payload`），两类都是现状。
- router 函数用端点语义名（`auth_status` / `overview` / `create_channel`），不带 `api_` 前缀；与导入的 service / repository 函数重名时加 `_route` 后缀避让（`create_site_route` / `delete_site_route`）。
- Pydantic 模型 PascalCase + 后缀：`<资源><动作>Request`（`SiteCreateRequest` / `DiscoveryImportRequest`）；透传模型继承 `common.CompatibilityModel`（`extra="allow"`）；统一信封用 `common.SuccessResponse`。
- 类型标注：公共函数标全参数与返回值；新代码用内建泛型 + PEP 604 联合（`dict[str, Any]` / `str | None`），旧代码里的 `Optional[...]` 不必翻新。

### 前端（TS / Vue）

- 命名：组件 `PascalCase.vue`（公共件从 `components/ui/index.ts` 出口）；composable `useXxx.ts`；变量 / 函数 `camelCase`；常量 `UPPER_SNAKE_CASE`（`TOKEN_KEY`）。
- 不用 `enum`：枚举一律字面量联合（`type Platform = "newapi" | "sub2api"`）；对象形状 `type` / `interface` 都有先例，跟随所在文件；透传类型以 `[key: string]: unknown` 收尾。
- api 域文件与后端 router 同名对应（`lib/api/auth.ts` ↔ `api/routers/auth.py`）。

### 注释内容（前后端通用）

- 只写**为什么 / 约束 / 契约对应**，不复述代码做了什么。正例：`routers/auth.py` 里解释 `compare_digest` 对非 ASCII 密码会抛 `TypeError` 的那条注释。
- 注释语言跟所在端走：**后端英文、前端中文**；同一模块内保持一致，不要单方面翻译既有注释。
- 跨端契约互相点名：前端模块头注释标对应的后端文件（`/** 控制台鉴权 — 对应后端 routers/auth.py */`），后端涉及跨端契约时也点名前端文件。
- 不留死代码与 `// removed` 类注释（见上文前端「维护约定」）。

---

## 仓库结构（真实结构）

```
upstream/
├── app.py                       # 兼容启动入口：内部启动 backend.main:app
├── backend/                     # FastAPI 应用本体（规范见上文「后端规范」）
│   ├── main.py                  # FastAPI app + lifespan + 异常处理 + 路由挂载 + 静态文件
│   ├── api/
│   │   ├── routers/             # auth / monitoring / notifications / settings / session_sync / admin_sites
│   │   └── schemas/             # Pydantic v2 请求/响应模型
│   ├── core/                    # config / security / errors / state / time / normalize
│   ├── services/                # 业务 Service 层
│   ├── repositories/            # 表级仓储（sites / admin_sites / changes / notifications）
│   ├── integrations/            # NewAPI / sub2api / SMTP / 企业微信 客户端门面
│   ├── workers/                 # SchedulerWorker（APScheduler）/ ModelCacheWorker
│   └── db/                      # PyMySQL 连接池（SQLAlchemy Engine）/ migrations / schema
├── apps/web/                    # 前端 Vue 3 控制台（规范见上文「前端规范」）
│   ├── src/                     # 入口、路由、页面、组件、composables、api 客户端、类型、token
│   ├── vite.config.ts           # 别名 @ → src，/api 代理到 8000
│   └── package.json
├── extensions/                  # 浏览器会话同步扩展（upstream-session-bridge）
├── docs/                        # 产品说明、浏览器同步剩余工作、superpowers 历史计划归档
├── design/                      # PriceAI 风格调研（历史归档）
├── tests/                       # 已有测试（默认不再新增）
├── AGENTS.md                    # 本文件（全仓库唯一权威规范）
├── README.md
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## UI（暖纸 + 浓墨）

- 唯一权威：本节 + `apps/web/src/styles/tokens.css`（token 语义、档位、动效时长都写在注释里）
- 页底 `#f4f1ea`（暗色 `#0d1011`），白卡片 `#ffffff`，品牌 veridian 绿 `#2c8a5a`（暗色 `#5cba89`）
- 标题可用宋体 `font-serif`；正文无衬线（Noto Sans SC）
- 状态胶囊 + KPI + 高密度表；组件层只消费语义工具类，不写死 hex
- 暗色：`html[data-theme=dark]`
- 任何 UI 调整先在浏览器里验证（dev 模式 + 至少一遍主流程，浅色暗色都过），不要仅靠 typecheck 就声称完成

---

## 配置文件

- `.env` 已被 `.gitignore` 排除；密钥绝不入库。
- 业务配置（站点、推送、SMTP、Webhook）写在 MySQL，由 Web UI 维护。
- `.env` 仅承载运行期默认值：DB 连接（含 `DB_CONNECT_TIMEOUT` / `DB_READ_TIMEOUT` / `DB_WRITE_TIMEOUT`）、`DB_POOL_*`、HOST/PORT、APP_TIMEZONE、TZ、CONSOLE_PASSWORD、CONSOLE_SESSION_TTL、SCHEDULER_ENABLED、ENABLE_API_DOCS、SLOW_REQUEST_THRESHOLD_MS、UPSTREAM_HTTP_TIMEOUT、SEED_DEMO。
- Docker 下 `DB_USER` 保持 `root`（compose 内置 mysql 只建 root），自建非 root 用户见 `docker-compose.yml` 注释。

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
- **主站是 aiinfinite.online**：对主站执行任何测试 / 脚本 / 写操作前，先按 URL 匹配（含 `aiinfinite.online`）确认目标；数据库自增 id 会随重建 / 换库漂移，**禁止把 id 当身份依据**（「id=2」只是当前快照）。严禁用 `main.example` 之类假数据覆盖真实主站（2026-08 事故教训）。
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

- 不要把 UI 改成深色 Linear 风却声称「暖纸 + 浓墨」
- 不要为「好看」删监控字段与检测能力
- 硬性禁引（白名单纪律的红线）：Celery / RQ / Redis 队列、Django / Flask、Tortoise / 异步 ORM；SQLAlchemy 仅限连接池用途，禁写 ORM 模型
