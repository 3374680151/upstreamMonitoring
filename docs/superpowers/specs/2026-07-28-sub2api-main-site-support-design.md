# sub2api 主站渠道支持设计

## 背景

Upstream 当前有两类站点：

- `sites` 是普通监控站点，已经支持 NewAPI 与 sub2api。
- `admin_sites` 是用户自己的主站连接，目前只支持 NewAPI 管理接口。

本功能扩展的是 `admin_sites`。用户需要把自己的 sub2api 站点添加为主站，实时读取和管理 sub2api 的渠道配置。监控对象是 sub2api 的 `/api/v1/admin/channels`，不是 `/api/v1/admin/accounts` 账号池。

当前 NewAPI 主站行为必须保持兼容。sub2api 主站使用管理员邮箱和密码登录，不支持管理员 API Key、手动导入 token、2FA 或 Turnstile。

## 目标

- 在同一个“主站监控”入口添加 NewAPI 或 sub2api 主站。
- 使用平台适配器隔离两套认证、渠道和分组接口。
- sub2api 通过管理员邮箱密码登录，并自动复用、刷新或重新建立登录态。
- 实时读取 sub2api 全部渠道及其绑定分组、分组倍率和模型定价。
- 支持编辑 sub2api 渠道官方更新接口允许的完整配置。
- 支持启用和停用 sub2api 渠道。
- 禁止从本系统新建或删除 sub2api 渠道。
- 在总览页统一汇总 NewAPI 与 sub2api 主站健康状态。
- 保留现有 MySQL 数据、NewAPI 凭据、登录态和 API 行为。

## 非目标

- 不读取、展示或监控 `/api/v1/admin/accounts` 账号池。
- 不为 sub2api 渠道提供新建或删除能力。
- 不给 NewAPI 主站增加新的写操作。
- 不新增主站渠道定时快照、变化记录或邮件/企业微信通知。
- 不支持 sub2api 管理员 API Key。
- 不支持 sub2api 2FA、Turnstile 或手动导入 access/refresh token。
- 不修改普通监控站点 `sites` 的 NewAPI/sub2api 采集行为。

## 总体架构

现有 `/api/admin/sites` 和 `/api/admin/sites/:id/*` 路由保持为统一入口。后端读取 `admin_sites.platform`，再把操作分发给对应的平台适配器。

```text
统一主站 API
    |
    +-- platform=newapi  --> NewAPI 适配器 --> 现有 /api/channel 等接口
    |
    +-- platform=sub2api --> sub2api 适配器 --> /api/v1/auth/*
                                           --> /api/v1/admin/channels
                                           --> /api/v1/admin/groups/all
```

两个适配器提供相同的内部能力：

- 测试连接与验证管理员身份
- 建立或恢复认证状态
- 获取全部渠道
- 获取单个渠道详情
- 获取分组与倍率
- 更新渠道配置
- 启用或停用渠道
- 返回平台能力清单

路由层不直接拼装平台请求。平台专属字段、认证头、分页、响应解析和错误转换都由适配器负责。

## 数据模型与迁移

### `admin_sites`

在现有表上新增以下列：

```sql
platform VARCHAR(32) NOT NULL DEFAULT 'newapi',
sub2api_access_token TEXT,
sub2api_refresh_token TEXT,
sub2api_access_expires_at BIGINT
```

现有 `login_username` 和 `login_password` 继续复用：

- NewAPI：网页登录账号和密码，用于现有真实渠道 key 读取流程。
- sub2api：管理员邮箱和密码，用于 `/api/v1/auth/login`。

现有 `browser_login_last_error` 和 `browser_login_last_check_at` 也作为平台适配器的最近认证结果存储；API 对外统一映射为中性的 `login_last_error` 和 `login_last_check_at`。由于主站创建后平台不可修改，同一行不会混用两个平台的认证状态。

不新增 `auth_mode`。主站平台决定认证方式：NewAPI 继续使用现有管理员系统令牌；sub2api 固定使用管理员邮箱密码。

迁移必须遵守：

- 只执行缺列补齐，不重建或清空 `admin_sites`。
- 所有现有记录因默认值自动成为 `newapi`。
- 不改写现有 `access_token`、`access_user_id`、网页登录态或安全验证状态。
- sub2api token 只写入新增列，不复用 NewAPI 的 `browser_access_token` 和 Cookie 字段。

### 凭据输出

主站列表只返回存在性与状态，不返回任何密码或 token：

- `has_login_password`
- `has_sub2api_session`
- `login_last_error`
- `login_last_check_at`

编辑 sub2api 主站时，密码留空表示保持原密码。

## 平台能力模型

主站列表响应增加：

```json
{
  "platform": "sub2api",
  "platform_label": "sub2api",
  "capabilities": {
    "list_channels": true,
    "read_channel_detail": true,
    "edit_channel": true,
    "toggle_channel": true,
    "create_channel": false,
    "delete_channel": false,
    "channel_key": false,
    "channel_priority": false,
    "channel_weight": false,
    "group_rates": true,
    "model_pricing": true
  }
}
```

NewAPI 也返回同结构的能力清单，但保留现有字段和行为。前端必须按能力渲染操作，不能只按平台名称散落判断。

## sub2api 认证

### 首次登录

创建或测试 sub2api 主站时调用：

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "configured-admin-email",
  "password": "configured-admin-password",
  "turnstile_token": ""
}
```

登录成功后必须检查响应中的用户角色。只有管理员账号可以保存为可用主站；普通用户账号返回“账号可登录，但无主站管理权限”。

保存：

- `access_token`
- `refresh_token`
- 按 `expires_in` 计算的绝对过期时间
- 最近登录检查时间

### 请求认证

sub2api 管理请求使用：

```http
Authorization: Bearer <access_token>
```

每次请求按以下顺序取得可用 token：

1. access token 未接近过期时直接复用。
2. token 临近过期或上游返回认证失效时调用 `/api/v1/auth/refresh`。
3. refresh 成功后原子保存新的 access token、refresh token 和过期时间。
4. refresh 失败时使用保存的管理员邮箱和密码重新登录一次。
5. 重新登录仍失败则停止重试并记录错误。

同一 `admin_site_id` 使用进程内可重入锁串行刷新。refresh token 可能轮换，后到请求必须重新读取数据库中的最新 token，不能用旧值覆盖新值。

一次业务请求最多执行一次 refresh 和一次密码重登。

### 不支持的验证方式

若上游返回 `requires_2fa`、Turnstile 校验失败或同类验证码要求，统一返回可操作提示：

> 当前 sub2api 主站仅支持无 2FA/Turnstile 的管理员邮箱密码登录，请关闭额外登录验证后重试。

本功能不尝试绕过验证，也不保存一次性验证码。

## API 契约

### 主站 CRUD

保留：

- `GET /api/admin/sites`
- `POST /api/admin/sites`
- `PUT /api/admin/sites/:id`

新增统一测试入口：

- `POST /api/admin/sites/test`

sub2api 创建请求：

```json
{
  "name": "生产 sub2api",
  "platform": "sub2api",
  "base_url": "https://sub2api.example.com",
  "login_username": "admin@example.com",
  "login_password": "secret"
}
```

主站创建后 `platform` 不可修改。更新请求试图改变平台时返回 `409`。

### 渠道与分组

保留统一路径：

- `GET /api/admin/sites/:id/channels`
- `GET /api/admin/sites/:id/channels/:channel_id`
- `GET /api/admin/sites/:id/groups`
- `PUT /api/admin/sites/:id/channels/:channel_id`

sub2api 渠道列表适配器调用 `/api/v1/admin/channels`，自动拉取全部分页。搜索参数转为 sub2api 的 `search` 查询参数。

sub2api 分组适配器调用 `/api/v1/admin/groups/all`，将 `group_ids` 关联为名称、平台、状态和 `rate_multiplier`。

渠道响应包含公共字段和平台原生配置：

```json
{
  "id": 12,
  "source_platform": "sub2api",
  "name": "Claude 渠道",
  "description": "",
  "status": "active",
  "normalized_status": "active",
  "group_ids": [1, 2],
  "groups": [
    {"id": 1, "name": "默认组", "rate_multiplier": 1.0},
    {"id": 2, "name": "高级组", "rate_multiplier": 0.8}
  ],
  "model_pricing": [],
  "model_mapping": {},
  "billing_model_source": "requested",
  "restrict_models": false,
  "features": "",
  "features_config": {},
  "apply_pricing_to_account_stats": false,
  "account_stats_pricing_rules": [],
  "capabilities": {
    "edit": true,
    "toggle": true,
    "create": false,
    "delete": false
  }
}
```

NewAPI 渠道继续返回现有顶层字段，新增字段必须向后兼容。

### sub2api 更新白名单

`PUT /api/admin/sites/:id/channels/:channel_id` 对 sub2api 只接受：

- `name`
- `description`
- `status`
- `group_ids`
- `model_pricing`
- `model_mapping`
- `billing_model_source`
- `restrict_models`
- `features`
- `features_config`
- `apply_pricing_to_account_stats`
- `account_stats_pricing_rules`

空数组、空对象、空描述是有效更新，不能按假值丢弃。未知字段返回 `400` 并列出字段名。

启停使用相同更新接口，只提交：

```json
{"status": "active"}
```

或：

```json
{"status": "disabled"}
```

sub2api 的渠道 `POST` 和 `DELETE` 在后端返回 `405 Method Not Allowed`。NewAPI 的既有后端接口保持现状。

## 前端设计

### 添加与编辑主站

`AdminSiteFormDialog` 增加平台选择。创建后平台字段锁定。

NewAPI 表单保持现状。sub2api 只显示：

- 名称
- sub2api Base URL
- 管理员邮箱
- 管理员密码
- 测试连接

测试成功显示管理员身份和可见渠道总数。密码编辑留空时显示“已保存，留空不修改”。

### 主站选择

主站选项显示名称、平台徽章和 Base URL。页面标题与说明按当前主站平台变化，不再固定写“NewAPI 主站”。

### sub2api 渠道表

表格显示：

- 渠道名称、ID、描述
- 状态
- 绑定分组
- 分组倍率
- 模型数量和平台
- 计费模式和计费来源
- 编辑、启用/停用、手动刷新

不显示：

- 渠道 key
- key 匹配状态
- 权重
- 优先级
- NewAPI 渠道类型

页面继续使用 PriceAI token、紧凑表格、状态胶囊、等宽数字和浅色/暗色主题。宽表格使用稳定列宽和横向滚动，不能因动态字段改变布局。

### sub2api 渠道编辑器

编辑弹窗使用标签页，不嵌套卡片：

1. 基本信息：名称、描述、状态。
2. 绑定分组：多选分组，并显示每组倍率。
3. 模型定价：平台、模型、计费模式、输入/输出/缓存/按次价格和区间价格。
4. 模型映射：按平台编辑源模型到目标模型的映射。
5. 高级计费：计费来源、限制模型、features、features_config 和账号统计定价规则。

编辑器保存用户实际修改的字段。保存失败时保留输入和当前标签页；保存成功后关闭弹窗并重新读取渠道与分组。

### 主站健康

总览页把平台状态转换为统一语义：

- NewAPI `1` -> `active`
- NewAPI `2` -> `disabled`
- NewAPI `3` -> `error`
- sub2api `active` -> `active`
- sub2api `disabled` -> `disabled`

KPI 为：

- 渠道总数
- 运行中
- 已停用
- 异常

主站认证或渠道读取失败计入异常主站提示，不伪造为渠道停用。手动刷新失败时保留上一次成功数据并标注“当前为上次结果”。

## 错误处理

后端将上游错误转换为稳定分类：

- 凭据错误
- 非管理员账号
- 额外登录验证不受支持
- token 过期或撤销
- 上游限流
- DNS、连接、超时或 TLS 错误
- 非 JSON 或结构异常响应
- 渠道不存在
- 渠道字段校验失败
- 上游服务端错误

错误响应保留 HTTP 状态、上游错误码和清洗后的消息，不包含密码、token 或完整请求体。

更新操作不做乐观修改。只有上游确认成功后才更新页面；失败时保留编辑器内容。

## 安全边界

- 密码和 token 永不返回前端。
- 日志、慢请求记录和异常摘要过滤认证字段。
- Base URL 只允许 `http` 或 `https`，禁止 URL userinfo。
- 携带认证头的重定向只能留在同一 Origin。
- sub2api 更新使用严格字段白名单。
- sub2api 新建和删除在后端强制拒绝。
- 登录和刷新有次数上限，不对认证失败无限重试。
- 延续当前 MySQL 凭据保存模式；数据库和备份都按含密钥数据管理。
- 不新增第三方 Python 依赖，继续只使用标准库和 PyMySQL。

## 测试设计

### 后端

- 旧 `admin_sites` 记录迁移后仍为 NewAPI。
- sub2api 管理员登录成功。
- 密码错误返回凭据错误。
- 普通用户登录后被拒绝为主站管理员。
- 2FA 和 Turnstile 返回明确提示。
- 有效 access token 被复用。
- access token 过期后 refresh 成功且轮换 token。
- refresh 失败后只密码重登一次。
- 并发请求只执行一次 refresh。
- 主站列表不泄露密码或 token。
- 渠道分页被完整拉取。
- `group_ids` 正确关联名称和倍率。
- 模型定价、区间定价和映射被完整保留。
- 空数组和空对象可以清除配置。
- 未知更新字段被拒绝。
- sub2api 渠道新建和删除返回 `405`。
- sub2api 主站流程从未请求 `/api/v1/admin/accounts`。
- 现有 NewAPI 主站、渠道 key 匹配和普通监控测试保持通过。

### 前端

- 主站表单按平台切换字段。
- sub2api 只提交管理员邮箱密码配置。
- 编辑密码留空不清空保存值。
- 已创建主站的平台不可修改。
- sub2api 表格不渲染 key、权重和优先级。
- 绑定分组、倍率与模型定价正确展示。
- 编辑器生成正确的部分更新请求。
- 启用和停用状态正确映射。
- NewAPI/sub2api 混合健康 KPI 正确统计。
- 刷新失败保留上次成功数据。

### 验证命令

```bash
python3 -m unittest discover -s tests -v
node --test tests/web/*.test.mjs
npm --prefix apps/web run build
```

## 人工验收

1. 使用现有 MySQL 数据启动，确认所有 NewAPI 主站仍可读取渠道。
2. 添加一个 sub2api 主站并测试管理员邮箱密码登录。
3. 读取全部 sub2api 渠道及其分组倍率。
4. 编辑名称、描述、绑定分组、模型定价、模型映射和高级计费配置。
5. 停用并重新启用一个 sub2api 渠道。
6. 确认界面没有 sub2api 渠道新建、删除或账号池入口。
7. 重启 Upstream，确认保存的凭据可以恢复管理会话。
8. 返回 NewAPI 主站，确认现有列表、健康、倍率和优先级行为未改变。

## 验收标准

- 用户可以在统一主站入口添加 NewAPI 或 sub2api。
- sub2api 管理员邮箱密码可以建立并自动续期管理登录态。
- sub2api 渠道、绑定分组、倍率和模型定价完整显示。
- sub2api 渠道完整配置可以编辑，状态可以启停。
- sub2api 渠道不能在本系统中新建或删除。
- 任何 sub2api 主站操作都不访问账号池接口。
- NewAPI 主站和普通监控站点行为没有回归。
- 现有 MySQL 数据和凭据没有被清空或重写。
