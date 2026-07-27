# 主站网页登录自动续期与页面首次刷新设计

## 背景与根因

主站 NewAPI 的 dashboard access token（AT）有效期为 15 分钟，Refresh Cookie
对应的登录 Session 有效期为 30 天。Upstream 已经保存了 `new_api_refresh` Cookie、
Session ID 和 AT，但调用 `POST /api/user/auth/refresh` 时没有发送同源 `Origin`。
主站启用 `SESSION_COOKIE_SECURE=true` 后会因此返回
`403 AUTH_ORIGIN_FORBIDDEN`，AT 到期后 Upstream 只能要求重新登录和 2FA。

主站安全 proof 与登录 Session 是两个生命周期：proof 有效期为 5 分钟，不能使用
Refresh Cookie 自动续期。已经持久化的渠道 key 不依赖持续持有 proof；读取新渠道 key
或强制重新读取 key 时，proof 过期后仍需用户再次完成 2FA。

## 目标

- 在 30 天登录 Session 有效期内，使用轮换 RT 自动签发新 AT，并跨 Upstream 重启持久化。
- AT 正常时不调用 refresh，避免无意义的 RT 轮换和并发竞争。
- AT 临期或受保护接口明确返回 AT 过期时，自动刷新并最多重试一次。
- 每次进入“主站监控”页面时，对默认主站自动执行一次渠道匹配刷新。
- 切换主站时，对新选中的主站自动执行一次刷新。
- 搜索、编辑、删除后的普通列表重载不再次触发整页自动匹配。
- 不降低主站的 Secure Cookie、OriginGuard 或 2FA 安全要求。

## 后端设计

### 同源刷新请求

从主站 `base_url` 解析出精确 origin（`scheme://host[:port]`），刷新请求发送：

- `Cookie: new_api_refresh=...`
- `X-Auth-Session: <sid>`
- `Origin: <主站 origin>`

不接受调用方传入任意 Origin，也不读取新的密钥配置。Origin 只由已保存的主站 URL
生成；URL 无合法 HTTP(S) scheme 或 host 时返回可操作的配置错误。

### 续期时机

将“确保会话可用”和“执行 refresh”拆成独立职责：

1. 没有 AT/SID 时，只有携带用户输入的 2FA 验证码才允许走现有登录流程。
2. `access_expires_at > now + 60s` 时直接复用 AT。
3. 已知 AT 在 60 秒内到期且存在 RT Cookie 时执行 refresh。
4. 主站版本未返回过期时间（值为 0）时先复用 AT；受保护接口返回
   `401 AUTH_TOKEN_EXPIRED` 后强制 refresh。
5. refresh 成功后原子持久化新 AT、轮换后的 RT Cookie、SID 和过期时间。
6. 受保护的渠道 key 请求若明确返回 AT 过期，强制 refresh 后最多重试一次；
   其他 401、403、429、proof 错误和网络错误不做循环重试。

每个主站使用进程内互斥锁串行 refresh。进入锁后重新读取/检查传入站点的最新状态，
避免多个渠道请求同时轮换同一个 RT。主站自身提供 30 秒 refresh race 容错，但本项目
不依赖该窗口保证正确性。

### 错误处理

解析 refresh 响应中的 HTTP 状态、`code` 和 `message`，保存脱敏后的错误原因：

- `AUTH_ORIGIN_FORBIDDEN`：提示检查主站 URL/可信 Origin，而不是误报 Session 过期。
- `AUTH_SESSION_MISMATCH`：提示本地 SID 与 RT 不一致，需要重新完成网页登录。
- `AUTH_SESSION_REVOKED` / `AUTH_UNAUTHORIZED`：提示登录 Session 已失效并重新 2FA。
- 网络或 5xx：AT 尚未到期时继续使用；AT 已到期时报告刷新暂时失败。

日志、API 响应和测试断言均不得输出 AT、RT、Cookie、密码或 2FA 验证码。

## 前端设计

`ChannelsPage` 当前把逐行自动匹配放在通用 `load()` 中，导致搜索和 CRUD 后也会刷新
所有匹配。改为两个阶段：

1. `loadChannels` 只读取渠道、分组和已有匹配结果。
2. `refreshChannelMatches` 按行串行调用现有 match API，并沿用当前的逐行更新、失败时
   保留最近成功倍率、429 避让和错误展示逻辑。

页面为当前挂载周期维护已自动刷新的主站 ID 集合。主站首次选中时先加载列表，再调用
一次 `refreshChannelMatches`；React StrictMode 重放 effect 时由该集合阻止重复刷新。
切换到另一个主站会为该主站执行一次。离开页面再进入时组件重新挂载，因此默认主站
会再次自动刷新一次，符合“点击到主站监控时刷新一次”的要求。

搜索、编辑、删除和保存后的 `loadChannels` 不触发整页匹配；单行“匹配/刷新”按钮仍使用
强制刷新参数，行为保持不变。不新增页面轮询，也不接入 App 的 15 秒总览轮询。

## 数据与接口兼容

- 继续使用 `admin_sites.browser_access_token`、`browser_refresh_cookie`、
  `browser_session_id` 和 `browser_access_expires_at`，无需数据库迁移。
- 继续使用现有 `/api/admin/sites/:id/channels/:channelId/match` 契约。
- 不向前端返回任何 token、Cookie 或 SID。
- 不修改监控站点、sub2api 登录续期、邮件或企业微信流程。

## 验证方案

后端自动化测试覆盖：

- AT 未临期时不请求 refresh。
- AT 临期时 refresh 携带由主站 URL 派生的精确 Origin、Cookie 和 SID。
- refresh 成功后保存轮换后的 RT、AT、SID 与过期时间。
- `AUTH_ORIGIN_FORBIDDEN`、Session 撤销和暂时性失败分别返回正确错误。
- 渠道 key 请求遇到 `AUTH_TOKEN_EXPIRED` 时刷新并只重试一次。
- 并发/连续 ensure 不重复轮换已刷新会话。
- 无 RT 或 Session 真正失效时仍要求重新登录和 2FA。

前端验证覆盖：

- 首次进入页面自动匹配一次。
- StrictMode effect 重放不重复匹配。
- 切换主站后新主站自动匹配一次。
- 搜索和 CRUD 后只重载列表，不触发整页匹配。

最后运行 Python 单元测试、前端类型检查/构建，并用本地主站接口验证 refresh 返回成功且
数据库中的过期时间向后推进；验证过程只输出布尔状态、状态码和时间差，不输出令牌。
