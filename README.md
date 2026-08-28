# Upstream · 上游分组倍率监控

轻量级的 **上游 AI 中转站分组倍率监控面板**：盯 NewAPI / sub2api 站点的分组、倍率、描述等变化，定时采集 → diff → 记录 → 邮件 / 企业微信推送。

> 自 [upstream-ratio-watch](https://github.com/Regert888/upstream-ratio-watch) 迁移并以「暖纸 + 浓墨」风格重构前端。

## 技术栈

| 层 | 实现 | 说明 |
|----|------|------|
| 后端 | `backend/` + 根目录 `app.py` | FastAPI/Uvicorn + **MySQL**（PyMySQL，直连不使用 ORM） |
| 前端 | `apps/web/` | Vue 3 + Vite 7 + Tailwind 4（暖纸 + 浓墨风格） |
| 数据 | **MySQL** 数据库 | 站点 / 快照 / 变化 / 推送日志（连接信息走 `.env`） |
| 部署 | `python3 app.py` 或 Docker Compose | 后端直接托管前端 `dist` |

## 快速开始

前置：Python ≥ 3.11，Node ≥ 20，**MySQL ≥ 8.0**。

先安装依赖并配置数据库连接：

```bash
pip install -r requirements.txt          # FastAPI/Uvicorn + PyMySQL
cp .env.example .env                      # 填入 DB_PASSWORD 等（.env 已被 gitignore）
# 在 MySQL 中建库：CREATE DATABASE upstream CHARACTER SET utf8mb4;
```

### 方式一 · 本地开发（前后端分离热更新）

```bash
# 终端 1 — 后端 (http://127.0.0.1:8000)，自动读取 .env
python3 app.py

# 终端 2 — 前端 (http://127.0.0.1:5173，/api 代理到 8000)
cd apps/web
npm install
npm run dev
```

### 方式二 · 本地生产（后端托管前端）

```bash
cd apps/web && npm run build   # 产出 apps/web/dist
cd ../.. && python3 app.py     # 访问 http://127.0.0.1:8000
```

`app.py` 是兼容启动入口，内部启动 `backend.main:app`。生产镜像会托管
`apps/web/dist` 中的前端构建产物。也可以直接运行：

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

### 方式三 · Docker

Compose 自带 `mysql:8.4` 服务，密码从 `.env` 注入（不硬编码）：

```bash
cp .env.example .env            # 至少填 DB_PASSWORD
docker compose up -d --build    # http://<服务器IP>:8000
```

MySQL 数据持久化在命名卷 `mysql-data`，升级容器时请保留。Compose 已内置健康检查与 `depends_on` 等待数据库就绪。

> **端口冲突**：Compose 把 MySQL 映射到宿主机 `127.0.0.1:3306`。若本机已有别的 MySQL 占用 3306，在 `.env` 设 `DB_PORT=3307`（映射为 `127.0.0.1:3307->3306`，app 走内网 `mysql:3306` 不受影响），或删除 compose 中 mysql 服务的 `ports` 段。
>
> **数据库用户**：Docker 下 `DB_USER` 请保持 `root`（compose 内置 mysql 仅创建 root）；需要专用非 root 用户见 `docker-compose.yml` 注释。

## 配置

**业务配置全部在 Web UI 中完成并写入 MySQL 数据库，不走 `.env`**：

1. **站点表单** — 名称、平台（NewAPI / sub2api）、Base URL、检测间隔、启用开关、认证字段
2. **推送设置页** — 企业微信 Webhook；SMTP 全套字段 + SSL + 测试发送
3. **历史数据** — snapshots / changes / notification_logs

`.env`（已被 `.gitignore` 排除）用于数据库连接与运行时默认值（见 [`.env.example`](.env.example)）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `DB_HOST` | `127.0.0.1` | MySQL 主机（Docker 内为服务名 `mysql`） |
| `DB_PORT` | `3306` | MySQL 端口 |
| `DB_USER` | `root` | MySQL 用户（Docker 下保持 `root`；裸机可用任意已建用户） |
| `DB_PASSWORD` | 空 | MySQL 密码（**必填**，仅放本地 `.env`） |
| `DB_NAME` | `upstream` | 数据库名 |
| `HOST` | `127.0.0.1`（容器内 `0.0.0.0`） | 监听地址 |
| `PORT` | `8000` | 监听端口 |
| `APP_TIMEZONE` / `TZ` | `Asia/Shanghai` | 展示时区（`APP_TIMEZONE` 优先） |
| `CONSOLE_PASSWORD` | 空（不启用） | 控制台登录密码。设置后所有 `/api/*` 需登录 |
| `CONSOLE_SESSION_TTL` | `604800`（7 天） | 登录会话有效期（秒，最小 300） |
| `SCHEDULER_ENABLED` | `1` | 是否启动进程内定时检测线程 |
| `ENABLE_API_DOCS` | `0` | 是否开放 `/docs` 和 `/openapi.json` |

> ⚠️ MySQL 中含密码 / token / webhook 等密钥；数据库连接密码只放本地 `.env`（已 `.gitignore`），**请勿把 `.env` 或数据库导出提交到公开仓库**。

> 🔒 **访问控制**：留空 `CONSOLE_PASSWORD` 时控制台无鉴权，仅适合本地 `127.0.0.1` 或可信内网直连。**对公网/外网暴露前务必设置 `CONSOLE_PASSWORD`**（否则任何人都能读取上游信息并修改主站渠道配置），并配合 HTTPS 反向代理 / IP 白名单。

## 功能

- 站点 CRUD（NewAPI 公开分组 + 系统访问令牌增强；sub2api 账密 / 导入登录态）
- 按站点间隔定时检测 + 手动检测
- 快照 diff：分组增删、倍率、描述、专属、订阅、RPM、**模型上下架** 等
- **账户额度**：登录后读取 NewAPI `/api/user/self` 或 sub2api `/api/v1/auth/me`，展示余额 / 额度 / 订阅用量
- 变化列表 + 分组倍率弹窗 + 上游模型健康
- **统一主站入口**（`/api/admin/sites`，后端按平台适配）：
  - NewAPI：管理员系统令牌读取渠道与分组，支持优先级调整和渠道 key × 上游分组倍率匹配
  - sub2api：仅使用管理员邮箱/密码登录，读取渠道与分组，支持完整渠道配置编辑和启用/停用
  - sub2api 主站只访问 `/api/v1/admin/channels` 与 `/api/v1/admin/groups/all`，不会读取 `/api/v1/admin/accounts` 号池
  - 两个平台都不在本系统新建或删除主站渠道；主站页面采用实时读取、手动刷新和健康汇总
  - 主站数据不进入定时快照、倍率 diff 或邮件/企业微信通知
- 邮件（SMTP）+ 企业微信 Webhook 推送（含测试发送）
- 控制台登录鉴权（可选，见 `CONSOLE_PASSWORD`）
- 「暖纸 + 浓墨」风格控制台：浅色默认 + 暗色主题

## 目录结构

```text
upstream/
├── app.py                    # FastAPI 兼容启动入口
├── backend/                  # FastAPI 应用、路由、服务、数据库和集成层
│   ├── main.py               # lifespan、调度器和静态文件托管
│   ├── api/                  # APIRouter、鉴权依赖和兼容路由
│   ├── services/             # 业务 Service 和迁移适配层
│   ├── integrations/         # NewAPI / sub2api 集成门面
│   ├── db/                   # PyMySQL 连接和迁移入口
│   └── core/                 # 配置、鉴权、错误处理
├── apps/web/                 # Vue 3 控制台
│   ├── src/                  # 页面 / 组件 / composables / lib / tokens
│   ├── vite.config.ts        # 别名 @ → src，/api 代理到 8000
│   └── package.json
├── extensions/               # 浏览器会话同步扩展（upstream-session-bridge）
├── docs/product.md           # 产品说明
├── design/priceai-style.md   # 风格调研（历史归档）
├── AGENTS.md                 # Agent 全局规则（含前后端规范，唯一权威）
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## 前端约定

UI 为「暖纸 + 浓墨」风格：页底 `#f4f1ea`、品牌 veridian 绿 `#2c8a5a`、白卡片、状态胶囊 + KPI + 高密度表；设计 token 权威在 `apps/web/src/styles/tokens.css`，暗色通过 `html[data-theme=dark]` 切换。完整前端规范见根目录 `AGENTS.md`。

## 技术交流

QQ 群：`259844673`
