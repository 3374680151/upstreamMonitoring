# Admin Browser Session Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the main-site NewAPI dashboard session usable by rotating its refresh cookie into a new access token, and refresh channel matching exactly once when the main-site monitor page is entered.

**Architecture:** Add a small, locked browser-session refresh path inside `app.py`, deriving the required Origin from the configured main-site URL and persisting every rotated auth bundle. Protected key reads retry once only for `AUTH_TOKEN_EXPIRED`. Split the React page's list loading from its sequential channel matching and guard automatic matching per site for each component mount.

**Tech Stack:** Python 3 stdlib HTTP/threading/unittest, PyMySQL, React 19, TypeScript, Vite, Node test runner.

---

## File Map

- Modify `app.py`: Origin derivation, refresh locking/policy, refresh error mapping, one-time key-read retry.
- Modify `tests/test_newapi_channel_key_matching.py`: backend refresh and retry regressions.
- Create `apps/web/src/lib/automaticRefresh.ts`: tiny tested guard for one automatic refresh per site per mount.
- Create `tests/web/automatic-refresh.test.mjs`: Node test for the guard, including StrictMode-style repeated claims.
- Modify `apps/web/src/pages/ChannelsPage.tsx`: separate list loading from sequential matching and use the guard.
- No database migration and no API contract changes.

### Task 1: Refresh Cookie Rotates Access Token Safely

**Files:**
- Modify: `tests/test_newapi_channel_key_matching.py`
- Modify: `app.py:87-112`
- Modify: `app.py:1327-1516`

- [ ] **Step 1: Write failing refresh-policy tests**

Add focused tests that assert a still-valid AT makes no request, and a near-expiry AT sends an exact same-site Origin and persists the rotated bundle:

```python
def test_does_not_refresh_browser_access_token_before_refresh_window(self):
    site = {"id": 2, "base_url": "https://main.example", "browser_access_token": "at",
            "browser_session_id": "sid", "browser_refresh_cookie": "new_api_refresh=rt",
            "browser_access_expires_at": 4102444800}
    with patch.object(app, "request_json_with_headers", return_value=(False, {}, "unexpected", {})) as request:
        self.assertEqual(app.ensure_admin_site_browser_session(site), (True, None))
    request.assert_not_called()

def test_refreshes_near_expiry_with_same_origin_and_persists_rotation(self):
    site = {"id": 2, "base_url": "https://main.example/console", "browser_access_token": "old-at",
            "browser_session_id": "sid", "browser_refresh_cookie": "new_api_refresh=old-rt",
            "browser_access_expires_at": 1}
    response = (True, {"success": True, "data": {"access_token": "new-at",
                "access_expires_at": 4102444800, "session": {"sid": "sid"}}}, None,
                {"set-cookie": ["new_api_refresh=new-rt; Path=/api/user/auth; HttpOnly; Secure"]})
    with patch.object(app, "db_query_one", return_value=None), \
         patch.object(app, "request_json_with_headers", return_value=response) as request, \
         patch.object(app, "db_execute"):
        self.assertEqual(app.ensure_admin_site_browser_session(site), (True, None))
    self.assertEqual(request.call_args.kwargs["headers"]["Origin"], "https://main.example")
    self.assertEqual(site["browser_access_token"], "new-at")
    self.assertEqual(site["browser_refresh_cookie"], "new_api_refresh=new-rt")
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_newapi_channel_key_matching.NewApiChannelKeyMatchingTests.test_does_not_refresh_browser_access_token_before_refresh_window tests.test_newapi_channel_key_matching.NewApiChannelKeyMatchingTests.test_refreshes_near_expiry_with_same_origin_and_persists_rotation -v`

Expected: the first test fails because current code refreshes on every call; the second fails because `Origin` is absent.

- [ ] **Step 3: Implement the minimal locked refresh path**

Add per-site locks and helpers with these interfaces:

```python
ADMIN_BROWSER_SESSION_LOCKS: Dict[int, threading.RLock] = {}
ADMIN_BROWSER_SESSION_LOCKS_GUARD = threading.RLock()

def _admin_browser_session_lock(site_id: int) -> threading.RLock:
    with ADMIN_BROWSER_SESSION_LOCKS_GUARD:
        return ADMIN_BROWSER_SESSION_LOCKS.setdefault(site_id, threading.RLock())

def _admin_site_origin(base_url: str) -> str:
    parsed = urlparse(normalize_base_url(base_url))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"

def _upstream_response_details(payload: Any, error: Optional[str]) -> Tuple[int, str, str]:
    status = int(payload.get("status") or 0) if isinstance(payload, dict) else 0
    code = str(payload.get("code") or "").strip() if isinstance(payload, dict) else ""
    message = _upstream_response_message(payload, error)
    raw = payload.get("raw") if isinstance(payload, dict) else None
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            code = code or str(parsed.get("code") or "").strip()
            message = str(parsed.get("message") or message).strip()
    return status, code, message

def _admin_browser_refresh_error(payload: Any, error: Optional[str]) -> str:
    _status, code, message = _upstream_response_details(payload, error)
    if code == "AUTH_ORIGIN_FORBIDDEN":
        return "主站拒绝刷新登录态：Origin 不受信任，请检查主站 URL 和可信 Origin 配置"
    if code == "AUTH_SESSION_MISMATCH":
        return "主站 RT 与 Session 不一致，请重新完成网页登录和 2FA"
    if code in {"AUTH_SESSION_REVOKED", "AUTH_UNAUTHORIZED"}:
        return "主站网页登录 Session 已失效，请重新完成网页登录和 2FA"
    return f"主站网页登录态刷新失败：{message or code or '未知错误'}"

def refresh_admin_site_browser_session(
    site: Dict[str, Any], force: bool = False
) -> Tuple[bool, Optional[str]]:
    with _admin_browser_session_lock(int(site["id"])):
        latest = db_query_one(
            "SELECT browser_access_token, browser_refresh_cookie, browser_session_id, "
            "browser_access_expires_at FROM admin_sites WHERE id = ?",
            (int(site["id"]),),
        )
        if latest:
            site.update(latest)
        expires_at = int(site.get("browser_access_expires_at") or 0)
        if not force and expires_at > int(time.time()) + 60:
            return True, None
        origin = _admin_site_origin(str(site.get("base_url") or ""))
        if not origin:
            return False, "主站 URL 无法生成有效 Origin"
        ok, payload, error, response_headers = request_json_with_headers(
            f"{normalize_base_url(str(site.get('base_url') or ''))}/api/user/auth/refresh",
            headers={"Cookie": str(site.get("browser_refresh_cookie") or ""),
                     "X-Auth-Session": str(site.get("browser_session_id") or ""),
                     "Origin": origin},
            method="POST",
        )
        if not ok or not isinstance(payload, dict) or not payload.get("success"):
            return False, _admin_browser_refresh_error(payload, error)
        auth_data, auth_error = _admin_browser_auth_data(site, payload, response_headers)
        if not auth_data:
            return False, auth_error or "主站刷新没有返回有效的网页登录态"
        _persist_admin_browser_auth(site, **auth_data)
        return True, None
```

`_admin_site_origin` must use `urlparse`, accept only HTTP(S), and return `scheme://netloc` without a path. `refresh_admin_site_browser_session` must lock by site ID, reload the four browser auth columns after acquiring the lock, skip refresh if another caller already advanced expiry, send Cookie/SID/Origin, parse the auth bundle, and call `_persist_admin_browser_auth` on success. Refactor `ensure_admin_site_browser_session` so valid ATs return before refresh, near-expiry ATs call the helper, and failed refresh only blocks once the AT is actually expired.

- [ ] **Step 4: Add and pass refresh error tests**

Add this `403 AUTH_ORIGIN_FORBIDDEN` regression and an equivalent
`401 AUTH_SESSION_REVOKED` case expecting “已失效” and renewed 2FA:

```python
def test_reports_origin_guard_failure_without_exposing_refresh_cookie(self):
    site = {"id": 2, "base_url": "https://main.example", "browser_access_token": "old-at",
            "browser_session_id": "sid", "browser_refresh_cookie": "new_api_refresh=secret-rt",
            "browser_access_expires_at": 1}
    response = (False, {"status": 403, "raw": json.dumps({
        "code": "AUTH_ORIGIN_FORBIDDEN", "message": "request origin is not allowed"})},
        "HTTP 403", {"set-cookie": []})
    with patch.object(app, "db_query_one", return_value=None), \
         patch.object(app, "request_json_with_headers", return_value=response), \
         patch.object(app, "db_execute"):
        ok, error = app.ensure_admin_site_browser_session(site)
    self.assertFalse(ok)
    self.assertIn("Origin", error)
    self.assertNotIn("secret-rt", error)
```

Run: `python3 -m unittest tests.test_newapi_channel_key_matching -v`

Expected: all channel matching tests pass.

### Task 2: Retry Protected Key Read Once After AT Expiry

**Files:**
- Modify: `tests/test_newapi_channel_key_matching.py`
- Modify: `app.py:1741-1845`

- [ ] **Step 1: Write a failing one-retry test**

Use this complete setup; a companion test changes the second response to another
401 and asserts `request.call_count == 2`:

```python
responses = [
    (False, {"status": 401, "raw": json.dumps({"code": "AUTH_TOKEN_EXPIRED"})}, "HTTP 401"),
    (True, {"success": True, "data": {"key": "sk-refreshed"}}, None),
]
site = {"id": 2, "base_url": "https://main.example", "browser_access_token": "old-at",
        "browser_session_id": "sid", "browser_refresh_cookie": "new_api_refresh=rt",
        "browser_access_expires_at": 4102444800}
with patch.object(app, "get_cached_admin_channel_key", return_value=""), \
     patch.object(app, "ensure_admin_site_browser_session", return_value=(True, None)), \
     patch.object(app, "refresh_admin_site_browser_session", return_value=(True, None)) as refresh, \
     patch.object(app, "request_json", side_effect=responses) as request:
    ok, key, error = app.fetch_newapi_channel_key(site, 10)
self.assertTrue(ok)
self.assertEqual(key, "sk-refreshed")
self.assertIsNone(error)
self.assertEqual(request.call_count, 2)
refresh.assert_called_once_with(site, force=True)
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m unittest tests.test_newapi_channel_key_matching.NewApiChannelKeyMatchingTests.test_retries_channel_key_once_after_access_token_expiry -v`

Expected: FAIL because the current function returns the first HTTP 401.

- [ ] **Step 3: Implement one-time retry**

Reuse `_upstream_response_details`, then use a two-attempt loop around the
protected request. Only the first `401 AUTH_TOKEN_EXPIRED` may force refresh and
continue; the second response exits through existing error handling:

```python
def _upstream_response_code(payload: Any) -> str:
    return _upstream_response_details(payload, None)[1]
```

- [ ] **Step 4: Run backend regression tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass.

### Task 3: Refresh Matches Once On Main-Site Page Entry

**Files:**
- Create: `apps/web/src/lib/automaticRefresh.ts`
- Create: `tests/web/automatic-refresh.test.mjs`
- Modify: `apps/web/src/pages/ChannelsPage.tsx:133-259`
- Modify: `apps/web/src/pages/ChannelsPage.tsx:310-545`
- Modify: `apps/web/src/pages/ChannelsPage.tsx:1157-1174`

- [ ] **Step 1: Write a failing guard test**

Create a Node test that imports `claimAutomaticRefresh` and verifies the first claim for a site is true, a repeated StrictMode-style claim is false, and another site is true:

```javascript
import assert from "node:assert/strict";
import test from "node:test";
import { claimAutomaticRefresh } from "../../apps/web/src/lib/automaticRefresh.ts";

test("claims one automatic refresh per site", () => {
  const claimed = new Set();
  assert.equal(claimAutomaticRefresh(claimed, 2), true);
  assert.equal(claimAutomaticRefresh(claimed, 2), false);
  assert.equal(claimAutomaticRefresh(claimed, 3), true);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run: `node --experimental-strip-types --test tests/web/automatic-refresh.test.mjs`

Expected: FAIL with module-not-found because `automaticRefresh.ts` does not exist.

- [ ] **Step 3: Implement the guard**

Create:

```typescript
export function claimAutomaticRefresh(claimedSiteIds: Set<number>, siteId: number): boolean {
  if (claimedSiteIds.has(siteId)) return false;
  claimedSiteIds.add(siteId);
  return true;
}
```

Run the Node test again; expected PASS.

- [ ] **Step 4: Split list loading from matching**

Extract the existing sequential row loop into this callback shape and move the
loop body unchanged into it:

```typescript
const refreshChannelMatches = useCallback(
  async (targetSiteId: number, channelList: Channel[], refreshVersion: number) => {
    for (const channel of channelList) {
      if (refreshVersion !== loadVersion.current) return;
      const response = await api.matchChannelUpstreamBinding(targetSiteId, channel.id);
      const data = response.data || {};
      setUpstreamBindings((previous) => ({
        ...previous,
        [String(channel.id)]: data,
      }));
    }
  },
  [],
);
```

Make `load` accept `{ refreshMatches?: boolean }`, load list/groups/bindings first,
and start the sequential matcher only when that option is true. Capture
`targetSiteId` at call start so switching sites cannot send later requests to
the wrong main site.

Use `useRef(new Set<number>())` plus `claimAutomaticRefresh` in the site-ID effect. The first claim calls `load("", { refreshMatches: true })`; repeated effects call list loading only. Ordinary search and CRUD calls keep `load()` with the default false. The 2FA `onVerified` callback must explicitly call `load("", { refreshMatches: true })` because verification is an intentional rematch.

- [ ] **Step 5: Verify frontend behavior**

Run: `node --experimental-strip-types --test tests/web/automatic-refresh.test.mjs`

Run: `npm --prefix apps/web run build`

Expected: guard test passes and TypeScript/Vite build succeeds.

### Task 4: Full Verification

**Files:**
- Verify only; do not edit unrelated files.

- [ ] **Step 1: Run all automated checks**

```bash
python3 -m unittest discover -s tests -v
node --experimental-strip-types --test tests/web/automatic-refresh.test.mjs
npm --prefix apps/web run build
git diff --check
```

Expected: all tests and build pass, and `git diff --check` prints nothing.

- [ ] **Step 2: Perform a credential-safe live refresh check**

Using the configured main-site row, call the production refresh helper once and print only success, error code/message, and whether `browser_access_expires_at` moved forward. Never print AT, RT, SID, Cookie, password, or 2FA code.

Expected: refresh succeeds and expiry moves forward by approximately 15 minutes, or a precise non-secret external configuration error is reported.

- [ ] **Step 3: Review the final diff**

Confirm no schema/API changes, no secret literals, no sub2api changes, and no unrelated user edits were reverted. Do not commit overlapping dirty files unless the user explicitly requests a commit.
