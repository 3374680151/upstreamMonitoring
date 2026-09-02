# 认证与登录态代码分析报告

> 生成时间：2026-08-31
> 分析范围：后端 `backend/` + 前端 `apps/web/` + 扩展 `extensions/`

---

## 一、整体架构概览

系统存在**两层完全独立的认证**：

| 层 | 用途 | 实现 |
|---|------|------|
| **控制台鉴权** | 保护本系统前端/后端访问 | 密码 + Bearer token（进程内存） |
| **上游站点登录态** | 管理上游 NewAPI/sub2api 站点的访问凭证 | browser/password/token 三种 auth_mode |

### 1.1 控制台鉴权（简述）

```
.env CONSOLE_PASSWORD 留空？
  ├─ 是 → 跳过鉴权
  └─ 否 → require_console_auth()
         ├─ 公开路径（login/logout/status/complete）→ 放行
         └─ Bearer token 有效？ → 放行 / 401
```

- 令牌：`secrets.token_urlsafe(32)` → 进程内存字典 `CONSOLE_SESSIONS`
- TTL：`CONSOLE_SESSION_TTL` 环境变量，默认 7 天
- 前端：`localStorage.console_token` + 401 广播 `console-unauthorized`

### 1.2 上游站点 auth_mode

| 值 | 含义 | UI 状态 | DB 默认值 |
|---|------|---------|----------|
| `"browser"` | 浏览器自动同步（推荐） | ✅ 默认 | 产品默认 |
| `"password"` | 账号密码登录 | ✅ 可选 | DB 列默认 `'password'` |
| `"token"` | 手动导入登录态 | ⚠️ 回填时归一化为 browser | 存量数据 |

---

## 二、已实现功能清单

### 2.1 认证回退链 ✅ 已实现

#### NewAPI 浏览器/密码模式（`newapi.py:1664-1821`）

```
┌─────────────────────────────────────────────────────────────┐
│  ① 令牌直连（access_token + user_id + X-Security-Proof）     │
│     ↓ 失败（401/403）                                       │
│  ② Refresh Cookie 自动续期 → 用新令牌重试                   │
│     ↓ 失败                                                  │
│  ③ 回退旧版 Cookie 会话（browser_cookie）                   │
│     ↓ 失败（401/403）                                       │
│  ④ 再次 Refresh Cookie 续期 → 重试                          │
│     ↓ 失败                                                  │
│  ⑤ 密码登录兜底（成功后落库新令牌）→ 系统兜底令牌            │
│     ↓ 全部失败                                              │
│  ⑥ 标记 expired，等待浏览器扩展重新同步                      │
└─────────────────────────────────────────────────────────────┘
```

**特殊处理**：
- WAF 拦截（`detect_waf_challenge_payload`）立即短路，不触发回退
- 网络错误、超时、429、5xx 不触发续期或回退

#### sub2api 浏览器模式（`sub2api.py:1581-1785`）

```
┌─────────────────────────────────────────────────────────────┐
│  ① 浏览器登录态 token 直连                                   │
│     ↓ 失败                                                  │
│  ② Refresh Token 旋转（CAS 防护）→ 重试                     │
│     ↓ 失败                                                  │
│  ③ 密码登录兜底（需配置 login_username/login_password）      │
│     ↓ 未配置密码                                             │
│  ④ 返回「需要浏览器同步」                                   │
└─────────────────────────────────────────────────────────────┘
```

#### token 模式（设计预期：无回退）

| 平台 | 行为 |
|------|------|
| NewAPI | 单次 `access_token + user_id`，失败返回「令牌已失效」 |
| sub2api | 有 refresh_token 时可旋转，无密码兜底 |

### 2.2 凭据保留规则 ✅ 已实现

**核心函数**：`persist_newapi_site_browser_session`（`newapi.py:1849-1892`）

```sql
login_username = CASE WHEN ? THEN login_username ELSE NULL END,
login_password = CASE WHEN ? THEN login_password ELSE NULL END
```

**调用点与行为**：

| 场景 | `preserve_login_credentials` | 效果 |
|------|------------------------------|------|
| 浏览器同步成功（CAS） | 固定 `NULL` | 清空密码 |
| 密码登录兜底 | `True` | 保留密码 |
| 浏览器模式续期 | `(auth_mode == "password")` | password 保留，browser 清空 |
| 同域名共享复用 | `True` | 保留密码 |
| 用户手动更新站点 | 前端控制 | 留空=保留，填写=替换 |

### 2.3 同域名登录态共享 ✅ 已实现

**核心函数**：`share_site_browser_session`（`session_sync_service.py:308-398`）

**流程**：
1. 检查目标站点 `auth_mode == 'browser'` 且无有效登录态
2. 提取目标站点的 `registered_domain(base_url)`（如 `www.text168.com` → `text168.com`）
3. 查询同平台、browser 模式、ready 状态的兄弟站点
4. 对目标站点 `validate_xxx_browser_session(target.base_url, candidate.token)` 校验
5. 通过后 `persist` 复用，返回 `{ shared: true, source_site_id }`

**支持平台**：
- NewAPI：`_share_newapi_site_browser_session`（`session_sync_service.py:226-305`）
- sub2api：内联逻辑（`session_sync_service.py:333-398`）

### 2.4 浏览器扩展协作 ✅ 已实现

#### 冷启动处理（`browserSessionBridge.ts:89-118`）

```typescript
// probeSessionBridge() 
// 首轮 400ms 探测失败 → 延长到 1600ms 再试一次
// 处理 MV3 service worker 冷启动可能超过首轮超时
```

#### 批量同步前置检测

- `autoResyncSiteBrowserSession`（第341-352行）：先静默探测扩展，不在线直接返回
- `syncSiteBrowserSession`（第251-273行）：先尝试同域共享，再创建请求
- `syncSiteSession`（第275-330行）：开头就 `probeSessionBridge()`，失败即 `reportBridgeFailure`

#### 完整同步生命周期

```
前端点击「同步」
  → syncSiteBrowserSession()
    → [1] tryShareSiteSessionSync（同域名复用，跳过扩展）
    → [2] createSessionSyncRequest（生成 request_id + secret）
    → [3] probeSessionBridge()（探测扩展）
    → [4] startSessionBridgeRequest → 发送到扩展
      → service-worker.handleStart()
        → ensureTargetTab()（打开/复用标签页）
        → readXxxTargetSession()（注入脚本读取登录态）
        → submitCompletion() → POST 到后端 complete
    → [5] 轮询 getSiteSessionSync() 等待终态
    → [6] 返回最终状态
```

**终态集合**：`ready`, `no_session`, `expired`, `permission_required`, `extension_unavailable`, `failed`

### 2.5 数据库 Schema ✅ 已实现

**`sites` 表关键字段**：

```sql
auth_mode VARCHAR(32) NOT NULL DEFAULT 'password',
login_enabled TINYINT NOT NULL DEFAULT 0,
login_username VARCHAR(255),
login_password TEXT,
access_token TEXT,
access_user_id VARCHAR(255),
refresh_token TEXT,
token_expires_at VARCHAR(40),
browser_refresh_cookie TEXT,
browser_cookie TEXT,
browser_session_id VARCHAR(255),
browser_access_expires_at BIGINT,
system_access_token TEXT,
system_token_fallback_enabled TINYINT NOT NULL DEFAULT 0,
session_sync_status VARCHAR(32) NOT NULL DEFAULT 'not_requested',
```

**一次性迁移**：
- `SUB2API_BROWSER_FIRST_MIGRATION`：sub2api 存量站点改为 browser 模式
- `NEWAPI_SYSTEM_TOKEN_FALLBACK_MIGRATION`：存量 newapi 站点开启 system_token_fallback
- `ADMIN_SITE_SYNC_CONFIG_MIGRATION`：主站同步配置迁移

---

## 三、部分实现 / 待完善功能

### 3.1 token 模式废弃 ⚠️ 部分实现

**已实现**：
- 前端回填时将 `"token"` 归一化为 `"browser"`（`SiteFormDialog.vue:74-77`）
- sub2api UI 下拉只暴露 `browser` / `password`（第574-575行）

**未实现**：
- 后端 `repositories/sites.py` 仍接受 `auth_mode="token"`（第274行校验）
- NewAPI UI 仍有 token 模式的输入框（`access_token` + `access_user_id`）
- 数据库列默认值仍为 `'password'`，部分代码默认 `'token'`
- `onToggleLoginEnabled(false)` 强制设 `auth_mode="token"`（第134行）

### 3.2 NewAPI 创建站点默认 auth_mode 不一致 ⚠️ 待统一

| 位置 | 默认值 |
|------|--------|
| `SiteCreateRequest` schema | `"password"` |
| `SiteFormDialog.vue` empty | `"browser"` |
| `db/schema.py` 列定义 | `'password'` |
| `monitoring.py` 更新时 | `"token"` |

### 3.3 sub2api 密码模式无回退 ⚠️ 设计待确认

`sub2api.py` 第1667-1676行：`auth_mode="password"` 只做一次登录，失败即返回，不进 refresh 或其他兜底。

---

## 四、逻辑问题与潜在风险

### 4.1 🔴 高风险：关闭认证增强导致站点退化

**位置**：`SiteFormDialog.vue:130-136`

```typescript
function onToggleLoginEnabled(loginEnabled: boolean) {
  form.value = {
    ...form.value,
    login_enabled: loginEnabled,
    auth_mode: loginEnabled ? form.value.auth_mode : "token",  // ← 问题
  };
}
```

**问题**：关闭「认证增强监控」时，browser 模式的站点会被强制转为 token 模式。但 NewAPI token 模式：
- 不使用 `system_access_token` 兜底
- 不触发 Refresh Cookie 续期
- 用户若留空 token 保存，后端校验会报错

**建议**：关闭认证增强时保持原 auth_mode 不变，或至少不切换到 token。

### 4.2 🟡 中风险：persist 默认清空密码

**位置**：`newapi.py:1853, 1869-1870`

```python
def persist_newapi_site_browser_session(
    ...
    preserve_login_credentials: bool = False,  # ← 默认清空
)
```

**问题**：任何未显式传 `True` 的调用都会把 `login_username/login_password` 置 NULL。当前所有调用点都显式传了 True，但接口默认值偏危险。

**建议**：考虑默认改为 `True`，或至少在 docstring 中强调必须显式传值。

### 4.3 🟡 中风险：token→browser 回填仅前端做

**位置**：`SiteFormDialog.vue:74-77`

**问题**：
- 前端编辑时将 `token` 显示为 `browser`
- 但数据库中仍是 `token`
- 未编辑的 token 站点在列表/详情里仍按 token 处理（`DetailPage.vue:256`）
- 存在「显示 browser、行为 token」的分裂窗口

**建议**：在后端提供一次性迁移脚本，将所有 `auth_mode='token'` 转为 `'browser'`。

### 4.4 🟡 中风险：setPlatform if/else 冗余

**位置**：`SiteFormDialog.vue:100-128`

```typescript
function setPlatform(platform: Platform) {
  form.value =
    platform === "sub2api"
      ? { ... }  // 完全相同的对象
      : { ... }; // 完全相同的对象
}
```

**问题**：两个分支代码完全相同，是冗余死代码。

**建议**：合并为一个分支。

### 4.5 🟢 低风险：sub2api 共享只取第一个候选

**位置**：`session_sync_service.py:374-375`

```python
for candidate in candidates:
    if registered_domain(candidate_base) == domain:
        break  # ← 取第一个就 break
```

**问题**：若第一个候选 token 已过期但 `token_expires_at` 未更新，会白跑一次校验后失败，不会尝试下一个候选。

**建议**：与 NewAPI 分支保持一致，遍历所有候选。

### 4.6 🟢 低风险：DB 默认值与产品默认值不一致

| 位置 | 默认值 |
|------|--------|
| `db/schema.py` | `auth_mode = 'password'` |
| `SiteFormDialog.vue` | `auth_mode = "browser"` |

**影响**：低，因为保存时总会显式传 `auth_mode`。

### 4.7 🟢 低风险：冷启动重试仅覆盖 probe

**位置**：`browserSessionBridge.ts:89-118`

**问题**：`probeSessionBridge` 有 400ms→1600ms 二次重试，但 `startSessionBridgeRequest` 没有。

**影响**：MV3 冷启动时，若 probe 通过但 start 时 service worker 刚好重启，可能超时。

---

## 五、优化建议

### 5.1 短期（低风险改动）

| 优先级 | 项目 | 说明 |
|--------|------|------|
| P1 | 修复 `onToggleLoginEnabled` | 关闭认证增强时不应强制切换到 token |
| P1 | 清理 `setPlatform` 冗余代码 | 合并 if/else 两个完全相同的分支 |
| P2 | 统一 auth_mode 默认值 | DB 列默认改为 `'browser'`，或确保所有代码路径显式传值 |
| P2 | sub2api 共享遍历所有候选 | 与 NewAPI 分支保持一致 |

### 5.2 中期（功能完善）

| 优先级 | 项目 | 说明 |
|--------|------|------|
| P1 | 彻底移除 token 模式 | 前后端删除 auth_mode="token" 相关逻辑，DB 一次性迁移 |
| P2 | 完善 sub2api 密码模式回退 | 考虑增加 refresh 或 browser 回退 |
| P2 | persist 默认值调整 | `preserve_login_credentials` 默认改为 `True` |

### 5.3 长期（架构优化）

| 优先级 | 项目 | 说明 |
|--------|------|------|
| P1 | 统一认证回退链抽象 | NewAPI 和 sub2api 的回退链模式相似，可抽取公共接口 |
| P2 | 扩展冷启动重试覆盖 start | `startSessionBridgeRequest` 也加二次重试窗口 |
| P2 | token 模式 DB 迁移 | 一次性将 `auth_mode='token'` 转为 `'browser'`，清理存量 |

---

## 六、文件索引

### 后端

| 文件 | 核心函数 |
|------|----------|
| `backend/core/security.py` | `require_console_auth()`, `console_authenticated()`, `create_console_session()` |
| `backend/core/config.py` | `CONSOLE_PASSWORD`, `CONSOLE_SESSION_TTL_SECONDS` |
| `backend/api/routers/auth.py` | `auth_status()`, `login()`, `logout()` |
| `backend/api/routers/session_sync.py` | `complete_session_sync()`, `create_session_sync_request()` |
| `backend/services/session_sync_service.py` | `share_site_browser_session()`, `persist_site_browser_session()` |
| `backend/integrations/newapi.py` | `newapi_request_with_auth()`, `validate_newapi_site_browser_session()`, `persist_newapi_site_browser_session()` |
| `backend/integrations/sub2api.py` | `_fetch_sub2api_with_auth_fallback()`, `validate_sub2api_browser_session()` |
| `backend/repositories/sites.py` | `update_site()`（含 auth_mode 校验） |
| `backend/db/schema.py` | `sites` 表定义 + 迁移逻辑 |

### 前端

| 文件 | 核心函数/组件 |
|------|--------------|
| `apps/web/src/composables/useAuth.ts` | `useAuth()` → 三态管理 |
| `apps/web/src/lib/api/client.ts` | `request()` + 401 处理 |
| `apps/web/src/lib/api/auth.ts` | `authApi.authStatus()`, `authApi.login()` |
| `apps/web/src/lib/api/sessionSync.ts` | `sessionSyncApi.createSiteSessionSync()` |
| `apps/web/src/lib/browserSessionBridge.ts` | `probeSessionBridge()`, `syncSiteBrowserSession()` |
| `apps/web/src/components/SiteFormDialog.vue` | auth_mode 切换逻辑 |
| `apps/web/src/components/SessionLoginAssistDialog.vue` | 登录引导弹窗 |

### 扩展

| 文件 | 职责 |
|------|------|
| `extensions/upstream-session-bridge/service-worker.js` | 核心逻辑：打开标签页 → 注入脚本 → 读取登录态 → POST 到后端 |
| `extensions/upstream-session-bridge/adapters/newapi.js` | NewAPI 协议适配 |
| `extensions/upstream-session-bridge/adapters/sub2api.js` | sub2api 协议适配 |

---

## 七、结论

当前认证与登录态系统**整体设计合理**，核心功能（回退链、同域名共享、扩展协作、凭据保留）均已实现。主要问题集中在：

1. **token 模式废弃不彻底**：前后端残留逻辑，需要一次性清理
2. **一个高风险交互 bug**：关闭认证增强时强制切换到 token 模式
3. **默认值不一致**：DB、schema、前端三处 auth_mode 默认值不同

建议按 P1 → P2 优先级逐步修复，其中 `onToggleLoginEnabled` 的 bug 应立即修复。
