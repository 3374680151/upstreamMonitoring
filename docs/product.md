# 产品说明

## 定位

Upstream 是上游 AI 中转分组倍率监控控制台：盯 NewAPI / sub2api 的分组与倍率变化，并推送告警。

## 已迁移功能（来自 upstream-ratio-watch）

| 模块 | 说明 |
|------|------|
| 站点管理 | 添加/编辑/删除；NewAPI、sub2api |
| NewAPI 采集 | 公开 `/api/user/groups`；可选系统访问令牌增强 |
| sub2api 采集 | 账号密码或导入 auth_token/refresh_token |
| 调度 | 按站点 interval 定时检测；可手动检测 |
| Diff | 倍率/分组增删/描述/专属/订阅/RPM/**模型上下架** 等 |
| 模型健康 | 站点模型接口缓存与弹窗展示 |
| 账户额度 | 登录后读取账户余额/额度/订阅用量（见下） |
| 推送 | SMTP 邮件 + 企业微信 Webhook + 测试发送 |
| 数据 | **MySQL**（`DB_*` 走 `.env`，运行期数据全部在 MySQL） |

## 登录后可拿到的信息（账户额度）

站点详情页「账户额度」面板按需实时读取该站点登录账号的信息：

| 平台 | 接口 | 字段 |
|------|------|------|
| NewAPI | `GET /api/user/self`（系统访问令牌 + New-Api-User） | 剩余额度、已用额度、请求次数、用户分组（额度按 500000=$1 折算 USD） |
| sub2api | `GET /api/v1/auth/me`（Bearer access_token） | 账户余额、冻结余额、累计充值、RPM 上限、订阅日/周/月用量与到期 |

> 该面板为按需查询（点击「查询」才请求上游），避免 sub2api 密码模式在检测循环里频繁登录。

## 模型上下架监控

检测时若该站点的模型缓存可用（用户查看过模型详情或启动预热），会记录每个分组的模型名单；
分组模型出现增删时产生 `model_added_to_group` / `model_removed_from_group` 变化并纳入告警。
新旧快照都必须带有模型名单才比较，缺失时安全跳过，不会误报整组增删。

## 配置入口（完整）

所有业务配置在 UI 中完成并写入 DB：

1. **站点表单**：名称、平台、Base URL、间隔、启用、认证字段（密码/token 编辑可留空表示不改）
2. **消息推送页**：企业微信开关+Webhook；SMTP 全字段+SSL+测试
3. **运行环境**：`HOST` / `PORT` / `APP_TIMEZONE`

## 两类「站点」（重要区分）

本控制台有两个**互相独立**的站点概念，切勿混淆：

| 概念 | 表 | 页面 | 作用 | 令牌 |
|------|----|------|------|------|
| **渠道（监控站点，即上游来源）** | `sites` | 总览 / 渠道监控 / 渠道详情 / 变化记录 / 余额 | 只读盯梢每个渠道：分组倍率、账户余额、历史变化 | 可选（认证增强 / 余额查询用） |
| **主站（管理站点，你自己的中转站）** | `admin_sites` | 主站监控（+ 总览页的「主站健康」） | 统一接入自己的 NewAPI / sub2api 后台，实时读取渠道和分组并按平台能力操作 | NewAPI：管理员系统令牌 + 用户 ID；sub2api：管理员邮箱 + 密码 |

- 渠道（监控站点）与主站**不共用令牌、不互相关联**；删除主站只从本控制台移除，不影响上游后台里的真实渠道。
- 监控站点进入定时采集、快照、diff 和通知；主站只实时读取、手动刷新和健康汇总，不产生快照、变化记录或推送。

### 页面职责

- **总览**：渠道统计 + 渠道概览 + 最近变化，末尾是**「主站健康」**面板——按统一状态聚合所有 NewAPI / sub2api 主站渠道的总数、运行中、已停用和异常。只有 NewAPI 原始状态 `3` 的自动停用渠道显示「重新启用」；sub2api 的 `disabled` 是正常停用状态，不计作自动故障。面板仅在进入页面时拉取一次（不跟随 15s 轮询），避免频繁打上游管理接口。
- **主站监控**：统一主站选择与 CRUD。NewAPI 保留渠道列表、分组倍率、真实 key 匹配和优先级调整；sub2api 提供渠道完整编辑、启用/停用、实时刷新，不允许新建或删除渠道。页面按后端能力字段渲染，不向 sub2api 发起 NewAPI 专属的 key、匹配、批量、测试接口。
- **余额**：按**你配置的渠道站点**、用各自登录态查询余额，**点按钮才查**（避免每次进页面都登录上游，
  sub2api 密码模式尤其敏感）。NewAPI 走 `/api/user/self`，sub2api 走 `/api/v1/auth/me`；
  未配置登录的站点会明确提示原因，不计入合计。

### NewAPI 令牌权限（重要，已核对 new-api 源码）

系统访问令牌**继承所属账号的角色**（`middleware/auth.go`：`role = user.Role`，之后与 session 走同一个
`if role < minRole` 判断）。所以「访问令牌 vs 管理员登录」权限**一样大**，差别只在令牌属于谁。
`New-Api-User` 头不是权限限制器，只是校验它等于令牌本人的用户 ID（填别人的会 401）。

角色：`Guest=0 · Common=1 · Admin=10 · Root=100`

| 用途 | 接口 | 最低角色 |
|------|------|---------|
| 余额 | `/api/user/self` | 普通用户 1 |
| 分组倍率 | `/api/user/self/groups` | 普通用户 1 |
| 公开分组 | `/api/user/groups` | 无需鉴权 |
| 价格 / 性能 | `/api/pricing`、`/api/perf-metrics*` | 默认匿名 |
| 渠道管理 | `/api/channel/*` | 管理员 10 起 |

**rc.20+ 的权限边界**：渠道接口在 admin 之上还有一层 casbin 细分权限。当前控制台不发起 NewAPI 渠道新建或删除，也不提供完整渠道编辑，只使用列表、分组、优先级更新和按需 key 匹配能力。请为主站令牌授予这些实际使用的最小权限。

**最小权限拆分**：
- **NewAPI 主站**（自己的中转站）→ 具备渠道读取/写入权限的管理员系统令牌；真实 key 匹配另配网页登录账号/密码，遇到 2FA 时按页面提示完成验证。
- **sub2api 主站**（自己的中转站）→ 管理员邮箱/密码。系统不支持 API Key、手动 token 导入、2FA 或 Turnstile 流程，因此这类验证开启时连接会失败。
- **渠道监控站点**（别人的上游）→ **普通用户令牌足够**，切勿填管理员令牌：那是第三方站点，
  令牌泄露会连带暴露渠道管理、用户管理与日志权限。

取分组必须 `/api/user/self/groups` 优先、`/api/user/groups` 兜底（见 [app.py](../app.py) 的
`fetch_newapi_groups_with_access_token`）——未鉴权打 `/api/user/groups` 时 `userId=0`，
拿到的是默认分组倍率而非该账号的真实倍率。

> 版本note：`New-Api-User` 头的强制要求在 v1.0.0-rc.22 移除，rc.21 及以前仍需要；我们始终发送该头，两边都兼容。

### sub2api 主站边界

- 登录：`POST /api/v1/auth/login`，会话续期使用 `POST /api/v1/auth/refresh`；只接受管理员邮箱和密码。
- 读取：只访问 `GET /api/v1/admin/channels` 与 `GET /api/v1/admin/groups/all`。
- 写入：只允许更新既有渠道的白名单配置字段和 `active` / `disabled` 状态。
- 禁止：不访问 `/api/v1/admin/accounts` 号池，不读取账户列表，不新建/删除渠道，不接受管理员 API Key 或手动 token。
- 渠道编辑涵盖基本信息、绑定分组、模型定价、模型映射和高级计费；账户统计规则只编辑配置中已有的账户 ID，不为此查询号池。

### 后端接口

- `GET/POST /api/admin/sites`、`PUT/DELETE /api/admin/sites/:id`（主站 CRUD）
- `POST /api/admin/sites/test`（按平台测试主站连接，不必先保存）
- NewAPI 主站真实 key 匹配前，后端使用 `/api/user/login`（必要时 `/api/user/login/2fa`）建立 Session，再调用 `/api/verify` 获取短期 `channel.key.read` proof。
- `GET /api/admin/sites/:id/channels`、`GET /api/admin/sites/:id/groups`、`PUT /api/admin/sites/:id/channels/:channel_id`（统一渠道薄代理，后端按平台分发）
- sub2api 对 key、匹配、测试、批量、新建和删除等 NewAPI 专属路由返回 `405`
- `GET /api/sites/:id/account`（余额页数据源：按站点登录态读账户余额/用量/订阅）

## UI 原则

「暖纸 + 浓墨」风格：见根目录 `AGENTS.md`「UI」章节与 `apps/web/src/styles/tokens.css`（`design/priceai-style.md` 仅为早期调研归档）。
