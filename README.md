# Upstream

上游 AI 分组倍率监控面板（自 [upstream-ratio-watch](https://github.com/Regert888/upstream-ratio-watch) 迁移并重构）。

- **后端**：Python 标准库 `app.py` + SQLite（无第三方依赖）
- **前端**：React + Vite + Tailwind（PriceAI 风格）
- **能力**：NewAPI / sub2api 站点监控、快照 diff、邮件/企业微信推送、模型健康

## 本地开发

### 1. 启动后端

```bash
cd ~/Desktop/upstream
python3 app.py
# http://127.0.0.1:8000
```

### 2. 启动前端（热更新）

```bash
cd ~/Desktop/upstream/apps/web
npm install
npm run dev
# http://127.0.0.1:5173  （/api 代理到 8000）
```

### 3. 生产一体启动

```bash
cd ~/Desktop/upstream/apps/web && npm run build
cd ~/Desktop/upstream && python3 app.py
# 访问 http://127.0.0.1:8000 ，后端直接托管 React dist
```

## Docker

```bash
docker compose up -d --build
# http://服务器IP:8000
```

数据卷：`./data`（含 `app.db` 与密钥配置，升级请保留）。

环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `HOST` | `127.0.0.1`（Docker 为 `0.0.0.0`） | 监听地址 |
| `PORT` | `8000` | 端口 |
| `APP_TIMEZONE` / `TZ` | `Asia/Shanghai` | 展示时区 |

## 功能清单

- 站点 CRUD（NewAPI / sub2api）
- NewAPI 公开分组 + 认证增强（系统访问令牌 / New-Api-User）
- sub2api 账号密码 / 导入登录态
- 定时检测 + 手动检测
- 分组倍率 diff（新增/删除/描述/专属/RPM 等）
- 分组倍率弹窗 + 上游模型健康
- 邮件 SMTP + 企业微信 Webhook
- PriceAI 风格控制台（浅色默认 + 暗色主题）

## 配置完整性说明

业务配置主要在 **Web UI → 数据库**，不是 `.env`：

1. **站点**：名称、平台、Base URL、间隔、启用、认证字段
2. **消息推送**：企业微信 Webhook、SMTP 全套
3. **历史**：snapshots / changes / notification_logs

迁移时已复制原项目 `data/app.db`（若存在）。请勿将含密钥的 `app.db` 提交到公开仓库。

## 目录

```text
upstream/
  app.py                 # 后端 + 调度
  data/app.db            # 本地数据（gitignore）
  apps/web/              # React 控制台
  AGENTS.md              # Agent 全局规则
  .agents/skills/priceai-ui/
  Dockerfile / docker-compose.yml
```

## 技术交流

QQ 群：`259844673`
