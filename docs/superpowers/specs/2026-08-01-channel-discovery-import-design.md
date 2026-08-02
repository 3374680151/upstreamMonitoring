# 主站渠道 URL 自动发现与监控导入设计

## 背景

Upstream 同时管理两类对象：`admin_sites` 下用户自己的主站及其真实渠道，和
`sites` 下用于独立定时监控的上游站点。当前主站页面已经可以完整读取 NewAPI
渠道列表，主站渠道匹配时也会按 `base_url` 复用 `sites` 中已有的监控登录态；但
用户仍然必须逐个复制 URL、打开“添加渠道”并重新填写配置。

本功能把现有能力串起来：从选定的 NewAPI 主站发现全部渠道 URL，去重后在现有
“添加渠道”流程中作为候选展示，用户选择后批量创建本地监控站点，并按现有浏览器
登录态桥接协议逐个同步认证。手动添加、编辑、检测、快照、变化记录、余额和通知
能力全部保留。

## 目标

- 从选定 NewAPI 主站读取全部渠道的 `base_url`，标准化并去重。
- 在“添加渠道”中提供“从主站发现”模式，不强制用户复制 URL。
- 明确展示候选的来源渠道、已存在状态和浏览器登录态处理状态。
- 支持单个导入和批量导入，导入过程幂等，重复刷新不会生成重复 `sites`。
- 导入后默认使用受控的浏览器登录态同步；没有登录态时保留待处理站点并支持重试。
- 保留现有手动 NewAPI/sub2api 添加、账号密码、token、手动检测和定时检测流程。
- 继续遵守浏览器同源和凭据边界：不读取浏览器密码库，不后台扫描标签页，不把
  token、Cookie 或密码返回给 React 页面。
- 兼容现有 MySQL 数据，只新增表和增量字段，不清空或重建业务表。
- UI 继续使用 PriceAI 视觉语言，浅色/暗色主题和窄屏布局都可用。

## 非目标

- 第一阶段不从 sub2api 主站自动发现渠道；sub2api 主站现有管理和手动监控流程不变。
- 不自动创建、删除或修改上游主站的真实渠道。
- 不在后台定时唤起 Chrome 或扫描所有标签页。
- 不尝试绕过 Turnstile、CAPTCHA、2FA 或其他交互式登录步骤。
- 不使用 Chrome 密码库、Chrome DevTools 远程调试端口或直接读取 Chrome Profile。
- 不把 NewAPI 管理员密码作为普通监控渠道的自动兜底方式。

## 现有基础与边界

- `fetch_all_newapi_channels()` 已经支持分页读取主站全部渠道。
- `normalize_base_url()` 是 URL 规范化的唯一入口，发现、匹配和导入都复用它。
- `find_monitor_site_for_channel()` 已按规范化后的 URL 查找已有启用监控站点。
- `browser_session_sync_requests`、Chrome 扩展和 `browserSessionBridge.ts` 已提供
  单个站点的单次凭证、同源标签页读取、服务端验证和状态轮询。
- `SiteFormDialog` 已保留 sub2api 浏览器模式下的可选邮箱/密码兜底；NewAPI 浏览器
  模式使用普通用户系统访问令牌和用户 ID，不新增密码直登入口。

本功能只增加发现与导入编排，不改变已有检测器、快照 diff、余额查询或通知调度器。

## 方案选择

### 方案 A：候选导入抽屉（采用）

刷新主站时只读取并展示候选，不自动写入 `sites`。用户勾选后，后端一次性创建缺失
站点，前端逐个调用现有浏览器同步流程。它在减少重复输入、控制数据写入和保持现有
操作习惯之间平衡最好。

### 方案 B：刷新即全部创建

每次主站刷新都把所有 URL 写入 `sites`。该方式看似省操作，但会产生大量未登录或
已经失效的监控记录，并让一次刷新产生不可预期的数据变化，因此不采用。

### 方案 C：仅建立主站渠道绑定

只在 `channel_upstream_bindings` 中保存来源关系，不创建 `sites`。它无法提供独立
定时检测、快照、变化记录和余额查询，不满足“添加渠道后直接监控”的目标。

## 总体架构

```text
SitesPage / SiteFormDialog
        |
        | 1. 选择 NewAPI 主站并读取候选
        v
  app.py + MySQL
        |
        | 2. URL 标准化、去重、关联已有 sites
        | 3. 用户确认后幂等创建 sites + 来源关联
        v
  React 导入编排器
        |
        | 4. 对每个新站点调用现有 session-sync 请求
        v
  localhost Chrome 扩展
        |
        | 5. 读取同 Origin 已打开标签页并直接提交后端
        v
  服务端验证会话 -> 保存 -> 首次检测
        |
        v
  候选行状态：已监控 / 已同步 / 待登录 / 失败
```

职责保持清晰：后端负责候选真实性、规范化、幂等写入和凭据验证；React 负责选择、
进度和展示；扩展只处理用户明确触发的单 Origin 会话读取。

## 数据模型

### `site_discovery_links`

新增来源关联表，避免在已有 `sites` 表中塞入主站语义，也保留一个监控站点对应多个
主站渠道的能力。

```sql
CREATE TABLE IF NOT EXISTS site_discovery_links (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    site_id INT NOT NULL,
    admin_site_id INT NOT NULL,
    channel_id INT NOT NULL,
    upstream_base_url VARCHAR(512) NOT NULL,
    channel_name VARCHAR(255),
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    UNIQUE KEY uq_site_discovery_channel (site_id, admin_site_id, channel_id),
    KEY idx_site_discovery_site (site_id),
    KEY idx_site_discovery_admin_site (admin_site_id),
    CONSTRAINT fk_site_discovery_site FOREIGN KEY (site_id)
        REFERENCES sites(id) ON DELETE CASCADE,
    CONSTRAINT fk_site_discovery_admin_site FOREIGN KEY (admin_site_id)
        REFERENCES admin_sites(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

导入已有 `sites` 时只新增关联，不覆盖用户手动填写的名称、间隔或认证。主站删除时，
关联记录由外键级联删除；远端主站渠道被删除时，下一次候选刷新会把对应关联标记为
过期并清理关联记录，但绝不删除本地监控站点或历史快照。

### 候选对象（不落库）

后端返回的候选按规范化 URL 聚合：

```json
{
  "base_url": "https://provider.example",
  "name": "Provider A",
  "channel_ids": [12, 18],
  "channel_names": ["主渠道", "备用渠道"],
  "channel_count": 2,
  "existing_site_id": 7,
  "existing_site_status": "ready",
  "importable": true
}
```

`existing_site_status` 只反映本地数据库中的同步状态；浏览器是否有打开的标签页不在
候选加载阶段偷偷探测，而是在用户点击同步后由扩展按 Origin 返回。

## 后端 API

### 读取候选

```http
GET /api/admin/sites/:admin_site_id/channel-candidates?keyword=
```

约束：

- 仅接受已保存且平台为 NewAPI 的 `admin_site`。
- 使用现有主站认证读取全量渠道；搜索参数只影响展示过滤，不改变全量去重语义。
- 仅保留合法 HTTP(S) `base_url`，拒绝 URL userinfo、空值和不支持的协议。
- 使用 `normalize_base_url()` 去尾斜杠并按规范化值聚合。
- 同时查询 `sites`，返回已存在站点 ID 和脱敏状态，不返回任何凭据。
- 若上游分页失败或达到安全上限而无法确认全量，返回明确错误，不把截断列表伪装成
  完整结果。

响应：

```json
{
  "success": true,
  "data": [/* candidate */],
  "meta": {"total": 3, "source_channel_total": 42}
}
```

### 批量导入

```http
POST /api/sites/discovery-import
Content-Type: application/json
```

请求只包含来源主站和候选引用，不包含 token、Cookie 或密码：

```json
{
  "admin_site_id": 3,
  "interval_minutes": 3,
  "items": [
    {
      "base_url": "https://provider.example",
      "name": "Provider A",
      "channel_ids": [12, 18]
    }
  ]
}
```

服务端重新规范化 URL，逐项执行：

1. 校验主站存在且为 NewAPI。
2. 校验候选 URL 格式，并限制单次导入数量。
3. 在事务中按规范化 URL 查找已有 `sites`；不存在则创建 NewAPI、启用状态、默认
   `interval_minutes`、`login_enabled=1`、`auth_mode=browser` 的站点。
4. 写入或更新 `site_discovery_links`，不覆盖已有站点的认证和用户自定义字段。
5. 返回每项的 `created`、`existing`、`invalid` 或 `conflict` 状态及 `site_id`。

该端点不创建浏览器同步请求，也不返回会话凭证。React 收到站点 ID 后复用现有
`POST /api/sites/:id/session-sync/requests`，逐项触发扩展同步，避免引入第二套凭证协议。

### 来源查询

```http
GET /api/sites/:site_id/discovery-links
```

只返回主站名称、渠道名称/ID、关联时间和脱敏 URL，用于站点详情和导入结果回显；不
返回认证信息。

## 前端设计

### 添加渠道入口

`SiteFormDialog` 仍是唯一的本地监控站点表单，顶部增加紧凑的分段控件：

- `手动添加`：保留现有全部字段和认证模式。
- `从主站发现`：进入候选导入视图，不丢弃手动表单草稿。

候选视图采用一个宽面板而不是卡片套卡片：

- 工具栏：NewAPI 主站选择、刷新、搜索、关闭。
- KPI 条：发现、已监控、待添加、待处理登录态。
- 紧凑表格：选择框、来源渠道、Base URL、监控状态、登录态状态、行操作。
- 底部固定操作行：选中数量、`添加并同步`、取消。

状态使用 PriceAI 胶囊：翠绿表示已核验/可用，信息色表示已存在，警告色表示待登录，
危险色表示失败。URL 和数字使用等宽数字；表格在窄屏改为纵向行块，不强制页面整体
横向滚动。

### 批量过程

- 点击“添加并同步”后显示顺序进度和当前 URL。
- 每个站点先完成本地创建，再调用现有浏览器桥接；不在 React 状态中保存 token。
- 成功项标记“已同步”并可直接跳转编辑；无登录态项保留“打开登录页 / 重新同步”。
- 失败项不影响其他项；批量结束后弹窗不自动关闭，只有全部成功或用户主动关闭时才退出。
- 关闭后，`SiteTable` 的行内状态和现有“同步登录态”按钮立即可用。

### 认证面板

导入的 NewAPI 站点默认显示浏览器同步状态；编辑时可切换到普通用户系统访问令牌和
用户 ID。不得显示管理员密码字段。

sub2api 手动添加和现有浏览器模式继续显示可选的“兜底用户邮箱/密码”以及 token 模式，
不改变其认证回退顺序。

### PriceAI 对齐

- 页面底色、面板、边框、品牌绿和暗色值全部引用 `tokens.css`。
- 使用 `Panel`、`Badge`、`Button`、`Select`、`Input` 等现有组件，不创建第二套控件。
- 主操作每组只有一个高强调按钮；刷新、重试、打开链接使用 Lucide 图标并配合可见文字。
- 保持高密度表格、轻边框、紧凑间距和 `tabular-nums`，不引入营销式大幅 Hero 或渐变背景。
- 所有文本在 320px 及以上宽度可换行，不遮挡相邻控件；浅色和暗色状态对比均可读。

## 数据流与状态

```text
加载候选
  -> ready（候选已去重）
  -> 用户选择
  -> importing（创建/复用 sites）
  -> syncing（逐项 browser session sync）
  -> ready / no_session / extension_unavailable / expired / failed
```

本地 `sites.session_sync_status` 是最终状态来源。候选列表中的“已监控”只是将其映射
为展示状态，不另建一套登录状态枚举。任何同步错误都沿用现有脱敏错误码和“上次成功
数据仍可用”的行为，不清空快照、分组或变化记录。

## 安全与兼容

- 所有 API 继续受控制台鉴权保护；只有现有 `/api/session-sync/requests/*/complete`
  保持匿名回环完成入口。
- URL 不允许携带用户名或密码；候选来源由服务端主站 API 决定，客户端不能提交凭据。
- 导入请求不记录 token、Cookie、密码或完整请求体；日志只记录站点 ID、状态和脱敏 URL。
- 批量操作设置候选数量和请求体大小上限，避免一次性创建无限站点。
- MySQL 启动迁移使用现有增量 DDL 模式；不执行 `DROP`、清空或重建 `sites`、`snapshots`
  和 `changes`。
- 已有手动站点即使没有来源关联，也继续按现有 CRUD、调度、快照、diff、余额和通知路径运行。

## 错误处理

| 场景 | 页面行为 | 数据行为 |
|------|----------|----------|
| 主站认证失效 | 显示主站错误，保留上次候选 | 不写 `sites` |
| 渠道列表不完整 | 明确提示无法确认全量 | 不执行批量导入 |
| URL 无效/重复 | 行级标记并允许其他项继续 | 无效项不写入；重复项幂等 |
| 扩展未安装 | 行状态为“扩展不可用”，提供安装/重试提示 | 站点保留，状态为待处理 |
| 没有同源登录标签页 | 显示“未检测到浏览器登录” | 站点保留，不覆盖旧数据 |
| 会话验证失败 | 显示脱敏原因和重试 | 不覆盖最近有效会话 |
| 单项同步失败 | 只标记当前项 | 其他项继续 |

## 测试计划

### 后端

- 候选 URL 标准化、去重、来源渠道聚合和已有 `sites` 状态映射。
- 主站认证失败、分页截断、非法 URL、数量上限和重复导入。
- 新建站点默认字段、已有站点不覆盖认证、来源关联幂等和级联行为。
- `/api/admin/sites/:id/channel-candidates` 与 `/api/sites/discovery-import` 契约。
- 现有 NewAPI/sub2api 检测、浏览器会话、key 匹配、余额和通知测试回归。

### 前端与扩展

- 手动/发现模式切换不丢失草稿。
- 候选筛选、全选、已存在禁用、批量进度和单项失败重试。
- 不把 session payload 写入 React 可见状态、日志或 URL。
- 现有 `SiteFormDialog`、`SiteTable`、自动刷新和浏览器同步测试继续通过。
- `npm run build`、TypeScript 检查和扩展 Node 测试通过。

### 视觉验收

- 桌面宽度检查候选表、工具栏和进度状态无重叠。
- 320px、375px 和窄桌面宽度检查文字换行、按钮可用和表格可读。
- 浅色/暗色主题检查状态胶囊、错误文本、边框和输入控件对比度。
- 使用 Playwright 截图确认页面非空、弹窗可交互、批量状态能更新。

## 验收标准

- 用户从一个 NewAPI 主站刷新即可看到全部去重后的上游 URL。
- 已存在监控站点不会重复创建，且原有认证、间隔、快照和通知不被覆盖。
- 用户可以选择一个或多个候选导入，并看到每项独立的创建和登录态结果。
- 浏览器登录态成功后站点可直接进入现有定时监控；未登录时可以稍后重试。
- 手动添加、编辑、检测、删除、余额、快照 diff、模型健康和推送功能保持可用。
- 不读取浏览器密码库、不后台扫描标签页、不把敏感会话值显示给页面或写入日志。
- 现有 MySQL 数据无需清空即可升级，所有自动化测试和生产前端构建通过。
