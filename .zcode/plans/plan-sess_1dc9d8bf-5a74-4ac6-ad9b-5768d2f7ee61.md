## 目标

修复「VPN/本机出口 IP 变更后 sub2api 登录态(rt/at)被误标失效」的问题。根因已核实:后端把上游 WAF/盾的 403 拦截当成认证失败(`is_sub2api_auth_error` 对所有 401/403 返回 true,sub2api.py:439),连锁触发 rt 刷新→失败→`mark_site_browser_session_expired`,把仍然有效的凭据标成 expired。方案采用用户选定的「分层自愈」:先修误伤,再补自动重同步,最后让失效原因可解释。

## 分支

`git checkout master && git checkout -b feat/sub2api-session-self-heal`(验证通过前不合入 master、不推远程)

## 第一步:区分「WAF 拦截」与「真认证拒绝」(backend/integrations/)

1. **http 层补响应头透出**:检查 `request_json` / `request_json_with_headers`(backend/integrations/http.py)返回值,确保 sub2api 请求路径能拿到响应头(status code + headers);不足处参照 `request_json_with_headers` 模式补齐。同时在 http.py 增加纯函数 `detect_waf_challenge(status, headers, body)`:命中任一特征即判定 WAF 拦截——
   - `server` 头含 `cloudflare` 且响应体不是 JSON API 信封;
   - `cf-mitigated: challenge` / `cf-chl-bypass` 头;
   - 403/503/521/530 且 body 为 HTML(`<!DOCTYPE`/`<html`)或含 `jschl`/`challenge-platform`/`Just a moment` 特征。
2. **收紧 `is_sub2api_auth_error`(sub2api.py:423)**:status 401/403 分支改为——若 `detect_waf_challenge` 命中则返回 False;同时响应体为非 JSON HTML 时不再仅凭 403 判 auth。保留 JSON API 形态 401/403 与明确错误码(TOKEN_EXPIRED/TOKEN_REVOKED/UNAUTHORIZED 等)的判定。
3. **`classify_sub2api_auth_failure`(sub2api.py:449)增加返回值 `"waf"`**:在 auth 判定前先做 WAF 检测,命中即返回 `"waf"`,不推进任何降级链。
4. **`_fetch_sub2api_with_auth_fallback`(sub2api.py:1512)处理 `"waf"`**:不触发 rt 刷新、不触发密码登录、不改写任何凭据;先按现有 curl_cffi 回退机制(http.py:117-149,TLS-reset 回退扩展为「TLS reset 或 WAF 挑战」触发)重试一次;仍被拦则本轮返回明确错误信息「上游防护拦截(WAF),与登录态无关」,错误分类标记为 waf。
5. **`collect_site_groups`(backend/services/monitoring_service.py:250-265)**:维持只在真 auth 错误 / `browser_sync_required` 时调 `mark_site_browser_session_expired`;waf 类错误只记录原因、不改 `session_sync_status`。
6. **NewAPI 同款保护**:`newapi_browser_request`(newapi.py:1699)触发 401/403 强刷前,同样先过 `detect_waf_challenge`,被盾拦截时不强刷、不改凭据、不置失效。

## 第二步:失败原因分类落库 + 序列化

1. **schema 迁移**(backend/db/schema.py):`sites` 表加列 `session_failure_kind VARCHAR(32)`(NULL=正常;取值 `auth_expired` / `waf` / `network` / `interactive`),走现有加列迁移机制,纯增量、不动存量数据。
2. **仓储**(backend/repositories/sites.py):`mark_site_browser_session_expired` 与新 helper(如 `mark_site_session_failure`)写入该列;检测成功时清空。`site_summary` 序列化输出 `session_failure_kind`。
3. **API 契约同步改**(按仓库规则 router+schema+前端 api+页面一起):`backend/api/schemas/site.py` 的 SiteSummary 加字段;`apps/web/src/lib/types.ts` 的 SiteSummary 加 `session_failure_kind: string | null`(不用 any)。

## 第三步:自动重同步(浏览器在线时自愈)

1. **前端**(apps/web/src/lib/browserSessionBridge.ts + SitesPage.vue / DetailPage.vue):browser 模式且 `session_sync_status` 为 `expired` / 错误含 `browser_sync_required` 的站点,在页面加载时自动发起一轮 `syncSiteBrowserSession`(复用现有 share→create→probe→start→poll 链路);同一站点自动尝试间隔不小于 5 分钟(内存去抖即可,不建新表);扩展不在线(probe 失败)时静默跳过,UI 状态显示「等待浏览器在线后自动重试」。
2. **站点列表徽标**(apps/web/src/components/SiteTable.vue 的 sessionSyncLabel/Tone):区分三类文案——`auth_expired`→「登录态已失效,请重新同步」;`waf`→「上游防护拦截,与登录态无关」;`network`→「网络异常」。详情页(SiteDetail)同步展示。

## 第四步:验证(按仓库本地验证流程)

1. 功能分支上起后端 `python3 app.py` + 前端 `npm run dev`,确认 `/healthz`、`/api/sites` 正常。
2. 用真实 sub2api 站点走一遍:正常检测 → 人为构造 WAF 拦截场景(如在 VPN 切换后立即检测;或临时以 hosts/代理模拟 403 HTML 响应)→ 确认 session_sync_status 不再被置 expired、徽标显示「上游防护拦截」。
3. 确认真令牌失效场景(rt 撤销)仍会正确置 expired 并提示重新同步。
4. 交给用户验证主流程,通过后再合 master。

## 约束

- 不新增任何依赖(全部用现有 httpx/curl_cffi/标准库);不新增测试文件;迁移仅加列不删改存量;密钥不落日志(沿用现有 `_sanitize_sub2api_error_text` 脱敏);同步 DB/HTTP 调用维持现有 run_in_threadpool 模式。