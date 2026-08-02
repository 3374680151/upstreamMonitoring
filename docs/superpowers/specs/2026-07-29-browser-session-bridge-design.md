# 浏览器登录态自动同步设计

## 背景

Upstream 目前支持通过账号密码或手动粘贴令牌监控 NewAPI / sub2api 上游。
当 sub2api 开启 Cloudflare Turnstile 或 2FA 后，后端的标准库 HTTP 客户端无法完成
浏览器挑战，账号密码登录会返回 `TURNSTILE_VERIFICATION_FAILED`。用户即使已经在本机
Chrome 登录同一上游，Upstream 也无法直接读取另一个 Origin 的 Local Storage；这是
浏览器同源策略，而不是请求头或接口路径问题。

本功能增加一个本地 Chrome 扩展作为受控桥梁。用户添加或编辑渠道时，Upstream 按需
请求扩展读取目标域名的现有登录态，服务端验证后写入 MySQL，并立即执行首次检测。

功能分两阶段交付：

1. 首先支持 sub2api 标准 AT / RT 登录态。
2. sub2api 验收完成后，在同一扩展和同步协议上增加 NewAPI 适配器。

## 目标

- 添加 sub2api 渠道时默认提供“浏览器自动同步”认证方式。
- 仅在保存渠道或用户点击“同步登录态”时读取浏览器，不常驻扫描标签页。
- 浏览器已有有效登录态时自动导入 AT、RT 和过期时间，随后完成首次检测。
- 浏览器没有有效登录态时明确显示“没有登录态，请提前登录”。
- 登录态同步成功后，渠道与其他渠道使用相同的分组、倍率、模型、额度和检测操作。
- 扩展、页面和 API 都不展示或记录明文 AT、RT、Cookie、同步凭证。
- 保持现有账号密码与手动导入 token 模式可用。
- 兼容已有 MySQL 数据，只做增量加列和新建辅助表，不重建或清空表。
- 第二阶段支持 NewAPI 普通监控渠道，并复用现有 `admin_sites` 浏览器会话能力。

## 非目标

- 不自动破解、绕过或代替用户完成 Turnstile、CAPTCHA、2FA。
- 不读取浏览器密码库、自动填充密码或与目标站无关的 Cookie / Local Storage。
- 不在后台持续轮询全部标签页或全部站点。
- 不把 Chrome 远程调试端口作为日常运行依赖。
- 不把 AT、RT 或 Cookie 返回给 Upstream React 页面。
- 首阶段不申请 Chrome Cookie 权限，也不处理 NewAPI HttpOnly Refresh Cookie。
- 不支持把浏览器登录态同步到远程公网 Upstream；首版桥接仅连接本机回环地址。

## 方案选择

采用 Chrome Manifest V3 扩展按需同步。

未采用的方案：

- 纯网页读取：受同源策略限制，`127.0.0.1` 页面不能读取上游域名存储。
- Python 读取 Chrome Profile：文件格式、进程锁和系统密钥链依赖不稳定，也扩大了密钥
  读取范围。
- Chrome DevTools Protocol：要求使用远程调试参数启动 Chrome，暴露面更大且不适合
  日常部署。
- 常驻扩展轮询：本需求只发生在添加渠道或登录态失效后的人工重试，没有持续扫描的
  必要。

## 总体架构

```text
SiteFormDialog / 渠道行“同步登录态”
              |
              | 1. 创建一次性同步请求
              v
        Upstream app.py + MySQL
              |
              | 2. request_id + 单次 secret + 精确 origin
              v
   localhost Content Script -> Extension Service Worker
                                      |
                                      | 3. 查找同 Origin 标签页
                                      |    或创建临时后台标签页
                                      v
                            平台 Session Adapter
                                      |
                                      | 4. 直接 POST 到本地后端
                                      v
        服务端验证 auth/me + groups -> 原子保存 -> detect_site
              |
              | 5. 页面只轮询状态，不接收令牌
              v
       已同步 / 待登录 / 已失效 / 同步失败
```

职责边界：

- React 负责创建同步请求、通知扩展、轮询状态和展示操作，不处理令牌。
- Chrome 扩展只在一次明确同步请求中读取指定 Origin 的平台字段。
- 后端负责一次性凭证、上游验证、持久化、刷新与检测。
- MySQL 只保存已验证的长期登录态和脱敏同步状态。

## 触发位置与用户流程

同步入口放在现有 `SiteFormDialog` 的添加/编辑渠道流程，不放在全局定时任务或独立的
主站渠道页面。

### 添加 sub2api 渠道

1. 用户选择 `sub2api`，认证方式默认选择“浏览器自动同步”。
2. 用户填写渠道名称、Base URL、监控间隔并点击“保存”。
3. 后端先创建 `sites` 记录，`auth_mode=browser`，同步状态为 `pending`。
4. 前端为新 `site_id` 创建一次性同步请求并通知扩展。
5. 扩展读取同 Origin 登录态并直接提交后端。
6. 后端验证、保存并执行 `detect_site(site_id)`。
7. 首次检测成功后关闭弹窗、刷新列表。

先创建站点记录再同步，确保一次性凭证可以严格绑定 `site_id + platform + origin`，也
避免令牌暂存在 React 表单状态。同步失败时保留站点记录，用户无需删除重建。

### 编辑与重试

- 编辑已有 `auth_mode=browser` 渠道时显示最近同步状态和“重新同步”操作。
- 渠道列表只在待登录、已失效或同步失败时显示“同步登录态”按钮。
- 用户在上游完成登录后点击“重新同步”，创建新的单次同步请求。
- 正常、可自动刷新登录态的渠道不显示额外行内按钮，保持操作区紧凑。

### 没有登录态

扩展未读到必需字段时返回 `no_session`。页面和渠道列表统一显示：

> 没有登录态，请提前登录

弹窗提供：

- “打开上游登录页”：打开并聚焦目标 Base URL，登录与验证码由用户完成。
- “重新同步”：重新创建一次性请求。
- “稍后处理”：关闭弹窗，保留待处理渠道。

## 数据模型

### `sites` 增量字段

```sql
session_sync_status VARCHAR(32) NOT NULL DEFAULT 'not_requested',
session_sync_error TEXT,
session_synced_at VARCHAR(40)
```

`auth_mode` 现有字段增加语义值 `browser`，不改变列类型。支持状态：

- `not_requested`：未使用浏览器同步。
- `pending`：已创建同步请求，等待扩展。
- `validating`：后端正在验证登录态。
- `ready`：登录态已验证并保存。
- `no_session`：浏览器没有该 Origin 的有效登录态。
- `expired`：浏览器或数据库中的登录态已失效。
- `permission_required`：扩展没有目标 Origin 权限。
- `extension_unavailable`：页面没有检测到扩展。
- `failed`：其他已脱敏失败。

`auth_mode=browser` 表示“浏览器登录态优先”，而不是与账号密码互斥：存在 AT 时先使用
Bearer token；AT 失效时使用 RT 刷新并持久化轮换结果；刷新失败且站点仍保存有账号密码
时，再执行一次账号密码登录作为兜底，并持久化新会话。密码登录遇到验证码、2FA 或其他
交互式认证时，不覆盖最近一次成功倍率，返回“请先在浏览器登录并同步”的可恢复状态。
浏览器同步成功和旧站点迁移都必须保留已有账号密码。

定时检测只使用 MySQL 中已经保存的凭据，绝不在后台唤起 Chrome。只有添加、编辑和用户
主动点击手动检测时，React 页面才允许在可恢复认证失败后调用扩展读取当前 Chrome 登录态，
同步成功后自动重试一次检测。这样后台任务保持确定性，浏览器权限也只在明确的人工操作中
使用。

### `browser_session_sync_requests`

```sql
CREATE TABLE IF NOT EXISTS browser_session_sync_requests (
    id VARCHAR(64) PRIMARY KEY,
    site_id INT,
    admin_site_id INT,
    platform VARCHAR(32) NOT NULL,
    target_origin VARCHAR(512) NOT NULL,
    secret_hash VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    error_code VARCHAR(64),
    error_message TEXT,
    expires_at VARCHAR(40) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    consumed_at VARCHAR(40),
    KEY idx_browser_sync_site_created (site_id, created_at),
    KEY idx_browser_sync_admin_site_created (admin_site_id, created_at),
    CONSTRAINT fk_browser_sync_site FOREIGN KEY (site_id)
        REFERENCES sites(id) ON DELETE CASCADE,
    CONSTRAINT fk_browser_sync_admin_site FOREIGN KEY (admin_site_id)
        REFERENCES admin_sites(id) ON DELETE CASCADE,
    CONSTRAINT chk_browser_sync_one_target CHECK (
        (site_id IS NOT NULL) <> (admin_site_id IS NOT NULL)
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

表中只保存随机凭证的 SHA-256 摘要，不保存明文 secret 或临时令牌。过期请求可在创建新
请求时顺便清理；不新增常驻清理线程。首阶段只创建 `site_id` 请求；第二阶段复用同一表
为 `admin_sites` 创建 `admin_site_id` 请求。两个目标列必须且只能有一个非空，删除任一
目标记录时由对应外键级联清理请求。

## API 契约

### 创建同步请求

```http
POST /api/sites/:site_id/session-sync/requests
Authorization: Bearer <console session>
```

成功响应仅在创建时返回一次明文 secret：

```json
{
  "success": true,
  "data": {
    "request_id": "random-id",
    "secret": "one-time-secret",
    "platform": "sub2api",
    "target_origin": "https://upstream.example.com",
    "expires_in": 60
  }
}
```

后端从已保存的 `sites.base_url` 生成精确 Origin，忽略调用方提供的 Origin。创建前验证：

- 站点存在且 `platform` 为受支持平台。
- `auth_mode=browser`。
- URL 为无用户名密码的合法 HTTP(S) URL。
- 同一站点只保留一个有效 `pending/validating` 请求；新建时使旧请求失效。

### 扩展完成同步

```http
POST /api/session-sync/requests/:request_id/complete
X-Upstream-Sync-Token: <one-time-secret>
Content-Type: application/json
```

sub2api 成功载荷：

```json
{
  "status": "session_found",
  "platform": "sub2api",
  "observed_origin": "https://upstream.example.com",
  "session": {
    "access_token": "...",
    "refresh_token": "...",
    "token_expires_at": "..."
  }
}
```

失败载荷不携带 session：

```json
{
  "status": "no_session",
  "platform": "sub2api",
  "observed_origin": "https://upstream.example.com"
}
```

该端点不使用永久控制台 token，只接受随机单次 secret。后端必须：

1. 常量时间比较 secret 摘要。
2. 校验请求未过期、未消费且状态允许完成。
3. 校验 platform 与 observed_origin 精确匹配请求记录。
4. 限制单个 token 和请求体大小，拒绝异常大载荷。
5. 原子抢占请求为 `validating`，重复提交返回已消费，不重复验证或写库。
6. 使用上游接口验证 session，失败时不覆盖任何最近有效登录态。
7. 验证成功后事务更新 `sites` 与同步请求状态。

接口响应只返回状态、错误代码和脱敏消息，不回显 session。

扩展正在查找标签页和读取页面期间，请求在数据库中保持 `pending`，React 使用本地状态
显示“正在查找浏览器登录态”。扩展提交 session 后，后端原子抢占为 `validating`。这样
不需要为高频进度更新开放额外的匿名写接口。

### 记录页面侧失败

```http
POST /api/sites/:site_id/session-sync/requests/:request_id/fail
Authorization: Bearer <console session>
Content-Type: application/json
```

该接口只允许前端提交服务端白名单代码：

- `EXTENSION_UNAVAILABLE`
- `ORIGIN_PERMISSION_REQUIRED`
- `SYNC_FAILED`

后端根据代码生成固定脱敏文案，不接受前端提交任意错误文本。接口验证请求属于该站点、
尚未消费且未过期，然后把请求和 `sites.session_sync_status` 更新为对应终态。扩展已经读到
目标页面但没有 session 时，不经过此接口，而是使用带单次 secret 的 complete 端点提交
`no_session`。

### 查询同步状态

```http
GET /api/sites/:site_id/session-sync/requests/:request_id
Authorization: Bearer <console session>
```

响应包含 `status`、`message`、`session_synced_at` 和首次检测结果摘要，不包含 secret 或
session。React 在弹窗打开期间按短间隔轮询，进入终态后停止。

## sub2api 扩展适配器

扩展目录：

```text
extensions/upstream-session-bridge/
  manifest.json
  service-worker.js
  content-script.js
  adapters/sub2api.js
  adapters/newapi.js       # 第二阶段加入
  popup.html
  popup.js
  icons/
```

首阶段权限：

- `tabs`：按精确 Origin 查找现有标签页。
- `scripting`：在目标页面上下文读取固定字段。
- `storage`：保存扩展版本与最小本地配置，不保存上游 session。
- 回环地址 host permission：与本地 Upstream 页面通信。
- 目标 HTTP(S) Origin 使用 optional host permissions 按站点授权。

扩展不在首阶段申请 `cookies` 或全站永久 host permission。

### 标签页选择

1. 使用 `${target_origin}/*` 查询已有标签页。
2. 优先选择最近活跃且可读取到完整 session 的标签页，不切换焦点、不刷新页面。
3. 没有同 Origin 标签页时创建一个 `active: false` 的临时标签页。
4. 临时标签页完成加载后读取 session，随后无论成功或失败都关闭。
5. 不自动打开可见登录页；只有用户点击“打开上游登录页”时才聚焦页面。

### 读取字段

sub2api 适配器仅读取目标 Origin Local Storage 中：

- `auth_token`
- `refresh_token`
- `token_expires_at`

必须存在非空 `auth_token`。`refresh_token` 缺失时允许提交，但后端将其标记为不可自动
续期。`token_expires_at` 允许上游常见的 epoch 毫秒、epoch 秒或 ISO 8601 字符串；服务端
统一解析为带时区的 ISO 8601 值后再写入现有 VARCHAR 字段。缺失或不可解析时不拒绝有效
session，而是把过期时间留空并以后端接口验证结果为准。

令牌只存在于扩展 service worker 的单次调用内存中。提交完成后不写入
`chrome.storage`，不发送回页面，不写 console。

### Upstream 页面握手

content script 只在回环地址页面提供版本化握手：

- 页面发送不含秘密的 capability probe。
- 扩展返回版本、支持平台和是否可处理同步请求。
- 页面在用户保存或重试操作中发送 request_id、单次 secret、平台和精确 Origin。
- service worker 完成后只向页面返回状态；真实 session 直传后端。

扩展不可用时，页面等待一个短的固定握手窗口，然后调用已鉴权的页面侧失败 API 设置
`extension_unavailable`，不继续无限重试。

## 服务端验证与持久化

### sub2api

后端依次验证：

1. 使用 AT 请求 `/api/v1/auth/me`，确认 session 可用。
2. 请求 `/api/v1/groups/available`，确认监控所需数据可读。
3. 尽力请求 `/api/v1/groups/rates`；该接口失败不否定有效 session，但保留脱敏警告。

验证成功后事务更新：

- `auth_mode = 'browser'`
- `login_enabled = 1`
- `access_token`
- `refresh_token`
- `token_expires_at`
- `session_sync_status = 'ready'`
- `session_sync_error = NULL`
- `session_synced_at`

事务完成后调用现有 `detect_site(site_id)`。检测结果继续写入快照、当前分组、状态和下一
检查时间。同步请求状态同时记录首次检测成功或检测失败摘要。

`session_sync_status=ready` 只表示浏览器登录态已验证并保存；渠道整体是否正常仍以现有
`sites.status` 和最近检测结果为准。若保存后的第二次检测遇到短暂网络错误，登录态保持
`ready`，渠道按现有逻辑显示 warning/failed。

验证失败时只更新同步状态与脱敏错误；不得清除或覆盖数据库中最近有效 AT / RT。

### Refresh token 轮换

`fetch_sub2api_user_groups`、模型查询和账户查询把 `browser` 视为 token 模式。现有每站点
刷新锁、短期刷新缓存和 `refreshed_auth` 持久化路径继续复用：

- 同一旧 RT 只轮换一次。
- 后到请求读取数据库中的新 RT。
- 新 AT / RT 原子写回。
- refresh 返回认证失效时设置 `session_sync_status=expired`。
- 网络或 5xx 不立即清空 session。

## 前端设计

### `SiteFormDialog`

sub2api 认证方式改为：

- 浏览器自动同步（默认）
- 账号密码
- 手动导入登录态

浏览器模式不显示邮箱、密码、AT 或 RT 输入框。显示紧凑的扩展连接行和状态胶囊，使用
现有 PriceAI token：

- `ready`：成功绿色胶囊“已同步”。
- `pending/validating`：信息色“同步中”。
- `no_session/permission_required`：警告色“待登录/待授权”。
- `expired/failed`：危险色“已失效/同步失败”。
- `extension_unavailable`：灰色“未连接扩展”。

保存后弹窗不立刻关闭，依次显示：

1. 正在查找浏览器登录态。
2. 正在验证登录态。
3. 已同步，正在执行首次检测。

成功后关闭。失败时保留弹窗与用户输入，并显示明确操作。用户选择“稍后处理”时关闭
弹窗但保留渠道记录。

### 渠道列表

为登录态增加紧凑状态展示，不新增大卡片：

- 正常渠道不增加额外操作按钮。
- 待登录、已失效或失败渠道显示带刷新图标的“同步登录态”。
- 错误文案完整显示或通过 title/详情查看，不能只显示 `HTTP 400`。
- 与现有状态、分组数、最近检测时间保持稳定列宽，移动端允许操作区换行而不重叠。

浅色与暗色主题均使用 `apps/web/src/styles/tokens.css`，品牌绿只用于成功状态与主要动作。

## 错误分类

| 代码 | 用户文案 | 行为 |
|---|---|---|
| `EXTENSION_UNAVAILABLE` | 未安装或未连接浏览器同步扩展 | 显示安装说明 |
| `ORIGIN_PERMISSION_REQUIRED` | 扩展需要该站点的读取权限 | 显示授权并重试 |
| `NO_SESSION` | 没有登录态，请提前登录 | 提供打开上游登录页 |
| `SESSION_EXPIRED` | 登录态已过期，请重新登录 | 保留旧 session，不覆盖 |
| `ORIGIN_MISMATCH` | 浏览器登录态与渠道域名不一致 | 拒绝写库 |
| `PLATFORM_MISMATCH` | 浏览器登录态平台不匹配 | 拒绝写库 |
| `SYNC_REQUEST_EXPIRED` | 登录态同步请求已过期，请重试 | 创建新请求 |
| `SYNC_REQUEST_CONSUMED` | 登录态同步请求已使用 | 不重复写库 |
| `UPSTREAM_FORBIDDEN` | 当前登录账号无读取权限 | 展示脱敏上游原因 |
| `UPSTREAM_UNAVAILABLE` | 上游暂时不可用，请稍后重试 | 保留旧 session |
| `SYNC_FAILED` | 登录态同步失败 | 展示脱敏详情 |

上游响应先经过现有敏感字段清洗，不将嵌套 raw body 直接返回前端。

## 第二阶段：NewAPI

第二阶段复用同步请求表、扩展握手、状态 UI 和安全校验，只增加 NewAPI 适配器。

NewAPI 存在两类前端认证实现：

1. 旧版将普通用户对象、系统访问令牌和用户 ID 保存在页面存储中。
2. 新版使用短期 AT、Session ID 与 HttpOnly Refresh Cookie，并通过同源 refresh 接口
   轮换。

### 普通监控渠道 `sites`

适配器按已知版本提取用户 ID 和普通用户认证状态。新版会在目标页面主世界执行同源
refresh 请求取得新 AT 与 Session 信息，并在用户单独授权后通过 Chrome Cookies API
读取指定 Origin、指定名称的 Refresh Cookie。

第二阶段为 `sites` 增加与 `admin_sites` 同语义的浏览器会话列：

- `browser_refresh_cookie`
- `browser_session_id`
- `browser_access_expires_at`

后端使用 `/api/user/self` 和分组接口验证普通用户身份，不接受管理员身份作为充分验证。
刷新请求的 Origin 只能由 `sites.base_url` 派生。

### 主站渠道 `admin_sites`

复用现有：

- `browser_access_token`
- `browser_refresh_cookie`
- `browser_session_id`
- `browser_access_expires_at`

扩展同步只补充已有网页登录 Session，不替代管理员系统访问令牌，也不自动完成 2FA
proof。渠道 key 的一次性 2FA 安全验证仍遵守现有流程。

### NewAPI 权限升级

sub2api 首阶段安装时不申请 Cookie 权限。只有用户首次选择 NewAPI 浏览器同步时才请求：

- `cookies`
- 目标 NewAPI Origin 的可选 host permission

扩展只读取明确列入 NewAPI 适配器的 Cookie 名称，不枚举其他站点 Cookie。

## 安全约束

- 一次性 secret 使用 `secrets.token_urlsafe` 生成，数据库只保存 SHA-256 摘要。
- secret 有效期 60 秒、单站点单活、一次消费，并使用常量时间比较。
- complete 端点限流、限制 JSON 深度和字段长度，不接受未知平台或任意 Origin。
- 扩展只连接触发请求的回环地址 Upstream Origin。
- extension request、token、Cookie、密码和验证码不写访问日志。
- 所有数据库更新使用参数化 SQL。
- 同步失败不删除历史快照、不清空最近有效 session。
- 扩展不把 session 写入 `chrome.storage`、剪贴板或 DOM。
- 前端类型只暴露 `has_*` 与同步状态，不增加任何明文 token 响应字段。
- 安装包为仓库内可审计源码，不加载远程脚本，符合 Manifest V3 CSP。

## 测试与验收

### 后端单元测试

- 创建同步请求只允许合法、已保存、`auth_mode=browser` 的站点。
- secret 摘要匹配、过期、重复消费和并发抢占。
- Origin、platform、site_id 不一致时拒绝。
- 超长 token、缺少 AT、未知字段和异常 JSON 拒绝。
- sub2api `/auth/me` 与 groups 验证成功后才写库。
- 无效 session 不覆盖最近有效 AT / RT。
- RT 轮换后原子保存，并发请求不使用旧 RT 覆盖新 RT。
- refresh 认证失败设置 `expired`，网络错误保留 session。
- API 响应和错误清洗不泄露 token、Cookie 或 secret。
- 既有 password/token 模式测试保持通过。

### 扩展逻辑测试

- 精确 Origin 标签页选择，不接受子串相似域名。
- 已有标签页不刷新、不聚焦。
- 无标签页时创建临时后台标签页并可靠关闭。
- sub2api 只读取三个白名单字段。
- optional permission 拒绝、无 session、后端超时和请求过期映射正确。
- service worker 不持久化 session，提交后清除内存引用。
- 页面握手只返回版本、能力和状态。

### 前端测试

- sub2api 新增渠道默认浏览器模式。
- 保存后创建站点、同步请求并进入状态轮询。
- 成功后关闭弹窗并刷新列表。
- 失败时保留表单并显示正确操作。
- “稍后处理”保留渠道记录。
- 异常渠道显示“同步登录态”，正常渠道不占额外操作空间。
- 深浅主题、桌面与窄屏无文字或按钮重叠。

### 真实浏览器验收

使用本地 Chrome 分别验证：

1. 已打开且已登录的 sub2api。
2. 未打开标签页但 Origin Local Storage 仍有有效登录态。
3. 没有登录态。
4. 未授权目标 Origin。
5. AT 过期但 RT 有效。
6. AT / RT 都失效。
7. 扩展未安装或被禁用。

每条流程检查数据库时只查询是否存在、状态和时间，不输出令牌内容。

### 完整验证命令

```bash
# 后端
python3 -m unittest discover -s tests

# 前端
cd apps/web && npm run build

# 扩展纯逻辑测试（实现阶段新增）
node --test extensions/upstream-session-bridge/tests/*.test.js
```

实现完成后启动后端和前端，用 Playwright 检查添加渠道弹窗、失败状态和渠道列表在桌面及
移动视口下的布局；真实扩展登录态流程使用本机 Chrome 人工完成一次端到端验收。

## 交付顺序

### 阶段一：sub2api

1. 数据库增量字段与同步请求表。
2. 一次性同步 API、验证器和状态输出。
3. Manifest V3 扩展、回环页面握手与 sub2api 适配器。
4. `SiteFormDialog` 浏览器模式与保存后同步流程。
5. 渠道列表状态与重试入口。
6. 后端、扩展、前端测试和真实 Chrome 验收。

阶段一全部通过后再进入阶段二，避免同时调试两套上游认证协议。

### 阶段二：NewAPI

1. NewAPI 旧版页面存储适配器。
2. 新版同源 refresh、Session ID 与精确 Cookie 读取。
3. `sites` NewAPI 浏览器会话刷新。
4. `admin_sites` 已有浏览器 Session 的扩展导入。
5. NewAPI 权限升级提示、测试和真实 Chrome 验收。

## 兼容与回滚

- 新列均有默认值，现有记录继续使用原 `password` 或 `token` 模式。
- 不修改或迁移现有明文认证字段的值。
- 扩展未安装时，账号密码与手动 token 模式继续工作。
- 禁用浏览器模式只改变目标站点认证方式，不删除历史快照和变化记录。
- 回滚前端或扩展时，后端保留已验证 token，仍可按 token 模式继续检测与刷新。
