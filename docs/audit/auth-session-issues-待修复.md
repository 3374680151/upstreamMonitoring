# 认证与登录态 - 审计问题清单

> 审计时间：2026-08-31
> 关联文档：[完整分析报告](./auth-session-analysis.md)

---

## 问题总览

| 编号 | 严重度 | 状态 | 问题摘要 |
|------|--------|------|----------|
| AUTH-001 | 🔴 高 | ✅ 已修复（2026-09-03） | 关闭认证增强导致站点退化 |
| AUTH-002 | 🟡 中 | ✅ 已修复（2026-09-03） | persist 默认清空密码 |
| AUTH-003 | 🟡 中 | ✅ 已修复（2026-09-03） | token→browser 回填仅前端做 |
| AUTH-004 | 🟡 中 | ✅ 已修复（2026-09-03） | setPlatform if/else 冗余 |
| AUTH-005 | 🟢 低 | ✅ 已修复（2026-09-03） | sub2api 共享只取第一个候选 |
| AUTH-006 | 🟢 低 | ✅ 已修复（2026-09-03） | DB 默认值与产品默认值不一致 |
| AUTH-007 | 🟢 低 | ✅ 已修复（2026-09-03） | 冷启动重试仅覆盖 probe |
| AUTH-008 | ⚠️ 待确认 | 待确认 | sub2api 密码模式无回退 |

---

## 🔴 高风险问题

### AUTH-001：关闭认证增强导致站点退化

**位置**：`apps/web/src/components/SiteFormDialog.vue:130-136`

**当前代码**：

```typescript
function onToggleLoginEnabled(loginEnabled: boolean) {
  form.value = {
    ...form.value,
    login_enabled: loginEnabled,
    auth_mode: loginEnabled ? form.value.auth_mode : "token",  // ← 问题
  };
}
```

**问题描述**：

关闭「认证增强监控」时，browser 模式的站点会被强制转为 token 模式。但 NewAPI token 模式：
- 不使用 `system_access_token` 兜底
- 不触发 Refresh Cookie 续期
- 用户若留空 token 保存，后端校验会报错

**复现路径**：

1. 编辑一个 browser 模式的 NewAPI 站点
2. 关闭「认证增强监控」开关
3. 保存 → auth_mode 变为 token，但 access_token 为空
4. 检测时后端报错：「使用系统访问令牌时需要填写 NewAPI 用户 ID」

**预期行为**：

关闭认证增强时应保持原 auth_mode 不变，或至少不切换到 token。

**修复建议**：

```typescript
function onToggleLoginEnabled(loginEnabled: boolean) {
  form.value = {
    ...form.value,
    login_enabled: loginEnabled,
    // 不再强制切换 auth_mode
  };
}
```

---

## 🟡 中风险问题

### AUTH-002：persist 默认清空密码

**位置**：`backend/integrations/newapi.py:1853, 1869-1870`

**当前代码**：

```python
def persist_newapi_site_browser_session(
    ...
    preserve_login_credentials: bool = False,  # ← 默认清空
)
```

**问题描述**：

任何未显式传 `True` 的调用都会把 `login_username/login_password` 置 NULL。当前所有调用点都显式传了 True，但接口默认值偏危险。

**调用点审计**：

| 调用位置 | 传值 | 风险 |
|----------|------|------|
| `_newapi_site_fallback_request`（密码兜底） | `True` | 安全 |
| `login_newapi_site_with_password` | `True` | 安全 |
| `refresh_newapi_site_browser_session` | `(auth_mode == "password")` | 安全 |
| `_share_newapi_site_browser_session` | `True` | 安全 |

**修复建议**：

将默认值改为 `True`，或在 docstring 中强调必须显式传值。

---

### AUTH-003：token→browser 回填仅前端做

**位置**：`apps/web/src/components/SiteFormDialog.vue:74-77`

**当前代码**：

```typescript
// 手动导入登录态（token）已废弃：旧数据回填时直接落到浏览器自动同步
auth_mode: (
  props.site.auth_mode === "token" ? "browser" : props.site.auth_mode
) as AuthMode,
```

**问题描述**：

- 前端编辑时将 `token` 显示为 `browser`
- 但数据库中仍是 `token`
- 未编辑的 token 站点在列表/详情里仍按 token 处理（`DetailPage.vue:256`）
- 存在「显示 browser、行为 token」的分裂窗口

**影响范围**：

- `DetailPage.vue:256`：`s.auth_mode === "token"` 时显示特殊提示
- `SiteTable.vue:415`：`site.auth_mode === 'token' ? '更新 Token' : '更新账号密码'`

**修复建议**：

提供一次性 DB 迁移脚本：

```sql
UPDATE sites SET auth_mode = 'browser' WHERE auth_mode = 'token';
```

---

### AUTH-004：setPlatform if/else 冗余

**位置**：`apps/web/src/components/SiteFormDialog.vue:100-128`

**当前代码**：

```typescript
function setPlatform(platform: Platform) {
  form.value =
    platform === "sub2api"
      ? {
          ...form.value,
          platform,
          login_enabled: true,
          auth_mode: "browser",
          login_username: "",
          login_password: "",
          access_token: "",
          refresh_token: "",
          token_expires_at: "",
          access_user_id: "",
        }
      : {
          ...form.value,
          platform,
          login_enabled: true,
          auth_mode: "browser",
          login_username: "",
          login_password: "",
          access_token: "",
          refresh_token: "",
          token_expires_at: "",
          access_user_id: "",
        };
}
```

**问题描述**：

两个分支代码完全相同，是冗余死代码。

**修复建议**：

```typescript
function setPlatform(platform: Platform) {
  syncResult.value = null;
  form.value = {
    ...form.value,
    platform,
    login_enabled: true,
    auth_mode: "browser",
    login_username: "",
    login_password: "",
    access_token: "",
    refresh_token: "",
    token_expires_at: "",
    access_user_id: "",
  };
}
```

---

## 🟢 低风险问题

### AUTH-005：sub2api 共享只取第一个候选

**位置**：`backend/services/session_sync_service.py:374-375`

**当前代码**：

```python
for candidate in candidates:
    if registered_domain(candidate_base) == domain:
        break  # ← 取第一个就 break
```

**问题描述**：

若第一个候选 token 已过期但 `token_expires_at` 未更新，会白跑一次校验后失败，不会尝试下一个候选。

**对比**：NewAPI 分支（第295-296行）会 `continue` 尝试下一个候选。

**修复建议**：

```python
for candidate in candidates:
    if registered_domain(candidate_base) == domain:
        # 校验失败时 continue 尝试下一个
        if not validate_ok:
            continue
        break
```

---

### AUTH-006：DB 默认值与产品默认值不一致

**位置**：

| 位置 | 默认值 |
|------|--------|
| `backend/db/schema.py:46` | `auth_mode = 'password'` |
| `apps/web/src/components/SiteFormDialog.vue:25` | `auth_mode = "browser"` |

**问题描述**：

DB 列定义默认 `'password'`，但前端新建站点默认 `'browser'`。当前低风险，因为保存时总会显式传 `auth_mode`。

**修复建议**：

将 DB 列默认值改为 `'browser'`：

```sql
ALTER TABLE sites MODIFY COLUMN auth_mode VARCHAR(32) NOT NULL DEFAULT 'browser';
```

---

### AUTH-007：冷启动重试仅覆盖 probe

**位置**：`apps/web/src/lib/browserSessionBridge.ts:89-118`

**问题描述**：

`probeSessionBridge` 有 400ms→1600ms 二次重试，但 `startSessionBridgeRequest` 没有。MV3 冷启动时，若 probe 通过但 start 时 service worker 刚好重启，可能超时。

**修复建议**：

在 `startSessionBridgeRequest` 中也加入重试逻辑。

---

## ⚠️ 待确认问题

### AUTH-008：sub2api 密码模式无回退

**位置**：`backend/integrations/sub2api.py:1667-1676`

**当前行为**：

`auth_mode="password"` 只做一次登录，失败即返回，不进 refresh 或其他兜底。

**待确认**：

这是设计预期还是遗漏？是否需要增加 refresh 或 browser 回退？

---

## 附录：相关文件索引

### 后端

| 文件 | 问题编号 |
|------|----------|
| `backend/integrations/newapi.py` | AUTH-002 |
| `backend/services/session_sync_service.py` | AUTH-005 |
| `backend/db/schema.py` | AUTH-006 |
| `backend/repositories/sites.py` | AUTH-003（相关） |

### 前端

| 文件 | 问题编号 |
|------|----------|
| `apps/web/src/components/SiteFormDialog.vue` | AUTH-001, AUTH-003, AUTH-004 |
| `apps/web/src/lib/browserSessionBridge.ts` | AUTH-007 |
| `apps/web/src/pages/DetailPage.vue` | AUTH-003（相关） |
| `apps/web/src/components/SiteTable.vue` | AUTH-003（相关） |

---

## 处理记录（2026-09-03，分支 `audit-cleanup-fixes`）

复核勘误：**AUTH-001 原复现路径不成立**。「使用系统访问令牌时需要填写 NewAPI 用户 ID」
的校验（`repositories/sites.py` create/update 两处）都以 `login_enabled` 为前提，
关开关后 `login_enabled=false`，保存与检测都不会报该错。真实问题是：
关开关会把 auth_mode 静默切成 token（此时认证方式下拉框被隐藏，用户不可见），
且因前端 `save()` 与后端都会从 `auth_mode=browser/password` 反推
`login_enabled=1`，出现「关掉 → 重开弹窗（token 回填显示 browser）→ 再保存 →
被悄悄重新开启」的循环。

- **AUTH-001**：前后端共 4 处对齐「开关是主控、auth_mode 只记录方式」——
  `onToggleLoginEnabled` 只动开关；`save()` 不再从 auth_mode 反推
  `login_enabled`；后端 create/update 的 login_enabled 计算去掉 auth_mode
  强制项（sub2api 保持强制开启）。浏览器会话同步成功落库仍置
  `login_enabled=1`（主动同步视为开启意图）。
- **AUTH-002**：默认值改 `True` + 英文 docstring 说明；现有 4 个调用点均
  显式传值，行为不变。
- **AUTH-003**：一次性迁移 `2026-09-03-sites-token-mode-to-browser`（记录于
  `app_schema_migrations`）：token 行归一 browser；有 access_token 的行同时置
  `session_sync_status='ready'`（对齐 sub2api browser-first 先例）。安全性依据：
  NewAPI 统一执行器 browser 模式主路径就是「access_token + New-Api-User」直连，
  与 token 模式同凭据、仅多 401 自愈。真实库 9 行 token 存量全部是
  `login_enabled=0` 的 parked 行，迁移对它们为纯归一化。注意：认证方式下拉框
  仍保留「手动系统访问令牌」选项，新选 token 的行首次编辑时仍会被回填为
  browser（废弃策略的延续），是否移除该选项待产品决策。
- **AUTH-004**：两相同分支合并。
- **AUTH-005**：候选循环内逐个校验，失败 `continue`，与 NewAPI 分支一致；
  全部失败时返回最后一个校验错误信息。
- **AUTH-006**：DDL 与 `SITES_COLUMN_ADDITIONS` 默认值改 `'browser'`；存量库
  一次性 `ALTER ... MODIFY`（Instant DDL），迁移名
  `2026-09-03-sites-auth-mode-default-browser`。
- **AUTH-007**：`startSessionBridgeRequest` 超时换新 correlation 重试一次；
  扩展 `handleStart` 无状态可重入 + 后端 complete 终态 CAS 兜底。
- **AUTH-008**：维持待确认。补充：存量 sub2api password/token 已被
  browser-first 迁移覆盖，且 sub2api 前端只提供 browser/password 两个选项，
  该路径实际触达面很小。
