# sub2api Main Site Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow one Upstream installation to add NewAPI and sub2api main sites, authenticate sub2api with an administrator email/password, read and edit sub2api channel configuration, and aggregate mixed-platform health without touching the sub2api account pool.

**Architecture:** Keep `/api/admin/sites` as the public contract and add platform-dispatch helpers inside `app.py`, following the existing single-file backend pattern. NewAPI keeps its current behavior; sub2api gets a separate persisted JWT lifecycle and adapters for `/api/v1/admin/channels` and `/api/v1/admin/groups/all`. The React UI consumes capability flags and uses a dedicated sub2api editor instead of adding platform conditionals throughout the NewAPI priority workflow.

**Tech Stack:** Python 3 standard library HTTP server, PyMySQL, `unittest`, React 18, TypeScript, Vite, Tailwind CSS, Lucide icons, Node test runner.

---

## File Map

- Modify `app.py`: schema migration, platform capabilities, sub2api administrator authentication, channel/group adapters, route dispatch, and method restrictions.
- Create `tests/test_sub2api_admin_sites.py`: backend regression coverage for authentication, token rotation, pagination, normalization, update allowlists, and account-pool exclusion.
- Modify `apps/web/src/lib/types.ts`: platform capabilities and sub2api channel/pricing types.
- Modify `apps/web/src/lib/api.ts`: unified main-site connection test and platform-aware update contracts.
- Create `apps/web/src/lib/sub2apiChannel.ts`: pure status, form, summary, and patch-building helpers.
- Modify `apps/web/src/components/AdminSiteFormDialog.tsx`: platform-aware main-site form and connection test.
- Create `apps/web/src/components/Sub2ApiPricingEditor.tsx`: structured model-pricing and interval controls.
- Create `apps/web/src/components/Sub2ApiChannelDialog.tsx`: tabbed full sub2api channel editor.
- Modify `apps/web/src/pages/ChannelsPage.tsx`: capability-driven NewAPI/sub2api rendering and actions.
- Modify `apps/web/src/components/MainSiteHealthPanel.tsx`: mixed-platform normalized health.
- Create `tests/web/sub2api-main-site.test.mjs`: frontend contracts and pure-helper tests.
- Modify `tests/web/main-site-channel-boundary.test.mjs`: retain NewAPI priority-only assertions while allowing the sub2api editor.
- Modify `README.md` and `docs/product.md`: document sub2api main-site authentication and channel-only scope.

Because the current workspace already contains unrelated modified and staged files, every commit step must first run `git diff --cached --name-status`. Do not stage an overlapping file if doing so would include pre-existing changes that are not part of this plan. In that case, leave the verified implementation uncommitted and report the exact files at handoff.

### Task 1: Add platform schema and capability metadata

**Files:**
- Modify: `app.py:406-507`
- Modify: `app.py:1775-1795`
- Create: `tests/test_sub2api_admin_sites.py`

- [ ] **Step 1: Write failing schema and capability tests**

Create `tests/test_sub2api_admin_sites.py` with:

```python
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import app


class Sub2ApiAdminSiteTests(unittest.TestCase):
    def test_admin_site_schema_additions_are_backward_compatible(self):
        self.assertEqual(app.ADMIN_SITE_COLUMN_ADDITIONS["platform"], "VARCHAR(32) NOT NULL DEFAULT 'newapi'")
        self.assertEqual(app.ADMIN_SITE_COLUMN_ADDITIONS["sub2api_access_token"], "TEXT")
        self.assertEqual(app.ADMIN_SITE_COLUMN_ADDITIONS["sub2api_refresh_token"], "TEXT")
        self.assertEqual(app.ADMIN_SITE_COLUMN_ADDITIONS["sub2api_access_expires_at"], "BIGINT")

    def test_admin_site_platform_defaults_to_newapi(self):
        self.assertEqual(app.admin_site_platform({}), "newapi")
        self.assertEqual(app.admin_site_platform({"platform": "sub2api"}), "sub2api")

    def test_admin_site_base_url_rejects_userinfo_and_non_http_schemes(self):
        self.assertEqual(app.validate_admin_site_base_url("https://sub.example"), ("https://sub.example", None))
        self.assertIsNotNone(app.validate_admin_site_base_url("https://user:pass@sub.example")[1])
        self.assertIsNotNone(app.validate_admin_site_base_url("file:///tmp/sub2api")[1])

    def test_sub2api_capabilities_forbid_create_delete_and_key_fields(self):
        capabilities = app.admin_site_capabilities({"platform": "sub2api"})
        self.assertTrue(capabilities["edit_channel"])
        self.assertTrue(capabilities["toggle_channel"])
        self.assertTrue(capabilities["model_pricing"])
        self.assertFalse(capabilities["create_channel"])
        self.assertFalse(capabilities["delete_channel"])
        self.assertFalse(capabilities["channel_key"])
        self.assertFalse(capabilities["channel_priority"])

    def test_admin_site_list_masks_sub2api_credentials(self):
        row = {
            "id": 9,
            "name": "sub main",
            "platform": "sub2api",
            "base_url": "https://sub.example",
            "login_username": "admin@example.com",
            "login_password": "password",
            "sub2api_access_token": "access",
            "sub2api_refresh_token": "refresh",
            "browser_login_last_error": None,
            "browser_login_last_check_at": "2026-07-28T12:00:00Z",
        }
        with patch.object(app, "db_query_all", return_value=[row]):
            payload = app.list_admin_sites_payload()[0]
        self.assertEqual(payload["platform"], "sub2api")
        self.assertTrue(payload["has_login_password"])
        self.assertTrue(payload["has_sub2api_session"])
        self.assertNotIn("login_password", payload)
        self.assertNotIn("sub2api_access_token", payload)
        self.assertNotIn("sub2api_refresh_token", payload)
```

- [ ] **Step 2: Run the tests and verify the missing symbols fail**

Run:

```bash
python3 -m unittest tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_admin_site_schema_additions_are_backward_compatible tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_admin_site_platform_defaults_to_newapi tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_admin_site_base_url_rejects_userinfo_and_non_http_schemes tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_sub2api_capabilities_forbid_create_delete_and_key_fields -v
```

Expected: `ERROR` because the new schema additions and helpers do not exist.

- [ ] **Step 3: Add additive schema columns and platform capabilities**

Extend both the `CREATE TABLE admin_sites` statement and `ADMIN_SITE_COLUMN_ADDITIONS` with:

```python
"platform": "VARCHAR(32) NOT NULL DEFAULT 'newapi'",
"sub2api_access_token": "TEXT",
"sub2api_refresh_token": "TEXT",
"sub2api_access_expires_at": "BIGINT",
```

Add these helpers next to `is_admin_site_row`:

```python
ADMIN_SITE_CAPABILITIES: Dict[str, Dict[str, bool]] = {
    "newapi": {
        "list_channels": True,
        "read_channel_detail": True,
        "edit_channel": True,
        "toggle_channel": False,
        "create_channel": False,
        "delete_channel": False,
        "channel_key": True,
        "channel_priority": True,
        "channel_weight": True,
        "group_rates": True,
        "model_pricing": False,
    },
    "sub2api": {
        "list_channels": True,
        "read_channel_detail": True,
        "edit_channel": True,
        "toggle_channel": True,
        "create_channel": False,
        "delete_channel": False,
        "channel_key": False,
        "channel_priority": False,
        "channel_weight": False,
        "group_rates": True,
        "model_pricing": True,
    },
}


def admin_site_platform(site: Dict[str, Any]) -> str:
    value = str(site.get("platform") or "newapi").strip().lower()
    return value if value in ADMIN_SITE_CAPABILITIES else "newapi"


def admin_site_capabilities(site: Dict[str, Any]) -> Dict[str, bool]:
    return dict(ADMIN_SITE_CAPABILITIES[admin_site_platform(site)])


def validate_admin_site_base_url(value: str) -> Tuple[str, Optional[str]]:
    normalized = normalize_base_url(value)
    try:
        parsed = urlparse(normalized)
        parsed.port
    except (TypeError, ValueError):
        return "", "主站 Base URL 无效"
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return "", "主站 Base URL 只允许 http 或 https"
    if parsed.username or parsed.password:
        return "", "主站 Base URL 不能包含用户名或密码"
    return normalized, None
```

Update `list_admin_sites_payload()` so each item includes:

```python
"platform": admin_site_platform(r),
"platform_label": "sub2api" if admin_site_platform(r) == "sub2api" else "NewAPI",
"capabilities": admin_site_capabilities(r),
"has_sub2api_session": bool(r.get("sub2api_access_token") and r.get("sub2api_refresh_token")),
"login_last_error": r.get("browser_login_last_error"),
"login_last_check_at": r.get("browser_login_last_check_at"),
```

Keep the existing NewAPI response fields for compatibility.

- [ ] **Step 4: Run the focused tests**

Run:

```bash
python3 -m unittest tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_admin_site_schema_additions_are_backward_compatible tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_admin_site_platform_defaults_to_newapi tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_admin_site_base_url_rejects_userinfo_and_non_http_schemes tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_sub2api_capabilities_forbid_create_delete_and_key_fields tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_admin_site_list_masks_sub2api_credentials -v
```

Expected: five tests pass.

- [ ] **Step 5: Commit the isolated change when staging is clean**

```bash
git add app.py tests/test_sub2api_admin_sites.py
git commit -m "feat: add main site platform metadata"
```

### Task 2: Implement persisted sub2api administrator sessions

**Files:**
- Modify: `app.py:136-142`
- Modify: `app.py:3208-3312`
- Modify: `tests/test_sub2api_admin_sites.py`

- [ ] **Step 1: Add failing administrator login and rotation tests**

Append:

```python
    def test_sub2api_admin_login_requires_admin_role(self):
        login_payload = {
            "code": 0,
            "data": {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "user": {"role": "user"},
            },
        }
        with patch.object(app, "admin_request_json", return_value=(True, login_payload, None)) as request:
            ok, auth, error = app.sub2api_admin_login(
                "https://sub.example", "user@example.com", "password"
            )
        self.assertFalse(ok)
        self.assertEqual(auth, {})
        self.assertIn("无主站管理权限", error)
        self.assertEqual(request.call_args.kwargs["payload"]["turnstile_token"], "")

    def test_sub2api_admin_login_returns_rotatable_session(self):
        login_payload = {
            "code": 0,
            "data": {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "user": {"role": "admin"},
            },
        }
        with patch.object(app, "admin_request_json", return_value=(True, login_payload, None)):
            ok, auth, error = app.sub2api_admin_login(
                "https://sub.example", "admin@example.com", "password"
            )
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(auth["access_token"], "access")
        self.assertEqual(auth["refresh_token"], "refresh")
        self.assertGreater(auth["access_expires_at"], int(app.time.time()))

    def test_ensure_sub2api_admin_session_refreshes_once_and_persists_rotation(self):
        site = {
            "id": 5,
            "platform": "sub2api",
            "base_url": "https://sub.example",
            "login_username": "admin@example.com",
            "login_password": "password",
            "sub2api_access_token": "old-access",
            "sub2api_refresh_token": "old-refresh",
            "sub2api_access_expires_at": 1,
        }
        refreshed = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }
        with patch.object(app, "db_query_one", return_value=site), \
             patch.object(app, "sub2api_admin_refresh_token", return_value=(True, refreshed, None)) as refresh, \
             patch.object(app, "db_execute") as save:
            ok, token, error = app.ensure_sub2api_admin_session(site)
        self.assertTrue(ok)
        self.assertEqual(token, "new-access")
        self.assertIsNone(error)
        refresh.assert_called_once_with("https://sub.example", "old-refresh")
        self.assertIn("new-refresh", save.call_args.args[1])

    def test_refresh_failure_relogs_once(self):
        site = {
            "id": 5,
            "platform": "sub2api",
            "base_url": "https://sub.example",
            "login_username": "admin@example.com",
            "login_password": "password",
            "sub2api_access_token": "old-access",
            "sub2api_refresh_token": "old-refresh",
            "sub2api_access_expires_at": 1,
        }
        auth = {"access_token": "login-access", "refresh_token": "login-refresh", "access_expires_at": 9999999999}
        with patch.object(app, "db_query_one", return_value=site), \
             patch.object(app, "sub2api_admin_refresh_token", return_value=(False, {}, "expired")), \
             patch.object(app, "sub2api_admin_login", return_value=(True, auth, None)) as login, \
             patch.object(app, "db_execute"):
            ok, token, error = app.ensure_sub2api_admin_session(site)
        self.assertTrue(ok)
        self.assertEqual(token, "login-access")
        self.assertIsNone(error)
        login.assert_called_once()

    def test_admin_redirect_handler_rejects_cross_origin_redirects(self):
        handler = app.SameOriginAdminRedirectHandler()
        request = urllib.request.Request(
            "https://sub.example/api/v1/admin/channels",
            headers={"Authorization": "Bearer secret"},
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://evil.example/collect",
            )
        self.assertEqual(raised.exception.code, 403)
```

- [ ] **Step 2: Verify the tests fail before implementation**

Run:

```bash
python3 -m unittest tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_sub2api_admin_login_requires_admin_role tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_sub2api_admin_login_returns_rotatable_session tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_ensure_sub2api_admin_session_refreshes_once_and_persists_rotation tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_refresh_failure_relogs_once tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_admin_redirect_handler_rejects_cross_origin_redirects -v
```

Expected: failures for missing `sub2api_admin_login` and `ensure_sub2api_admin_session`.

- [ ] **Step 3: Add main-site session locks and login parsing**

Add module-level locks:

```python
ADMIN_SUB2API_SESSION_LOCKS_GUARD = threading.RLock()
ADMIN_SUB2API_SESSION_LOCKS: Dict[int, threading.RLock] = {}
ADMIN_SUB2API_EXPIRY_SKEW_SECONDS = 60
```

Add a sub2api-admin-only request helper that rejects credential-bearing redirects to another Origin. Keep the existing general `request_json` unchanged so NewAPI and ordinary monitor behavior cannot regress:

```python
class SameOriginAdminRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source = urlparse(req.full_url)
        target = urlparse(newurl)
        source_origin = (source.scheme.lower(), source.hostname, source.port)
        target_origin = (target.scheme.lower(), target.hostname, target.port)
        if source_origin != target_origin:
            raise urllib.error.HTTPError(
                newurl, 403, "跨 Origin 跳转已拒绝", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def admin_request_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "GET",
) -> Tuple[bool, Any, Optional[str]]:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    opener = urllib.request.build_opener(SameOriginAdminRedirectHandler())
    try:
        with opener.open(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return True, json.loads(raw) if raw else {}, None
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}"
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc)


def sub2api_admin_refresh_token(
    base_url: str, refresh_token: str
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    ok, payload, error = admin_request_json(
        f"{normalize_base_url(base_url)}/api/v1/auth/refresh",
        payload={"refresh_token": str(refresh_token or "").strip()},
        method="POST",
    )
    if not ok:
        return False, payload if isinstance(payload, dict) else {}, error
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, dict):
        return False, {}, message or "刷新登录态失败"
    return True, data, None
```

Add the administrator-specific login function without changing the existing monitor-site `sub2api_login` behavior:

```python
def sub2api_admin_login(
    base_url: str, email: str, password: str
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    if not str(email or "").strip() or not str(password or ""):
        return False, {}, "sub2api 主站需要管理员邮箱和密码"
    ok, payload, error = admin_request_json(
        f"{normalize_base_url(base_url)}/api/v1/auth/login",
        payload={
            "email": str(email).strip(),
            "password": str(password),
            "turnstile_token": "",
        },
        method="POST",
    )
    if not ok:
        message = _upstream_response_message(payload, error)
        if "turnstile" in message.lower():
            message = "当前 sub2api 主站不支持 Turnstile 登录验证"
        return False, {}, message or "sub2api 主站登录失败"
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, dict):
        return False, {}, message or "sub2api 主站登录响应异常"
    if data.get("requires_2fa") or data.get("temp_token"):
        return False, {}, "当前 sub2api 主站不支持 2FA 登录验证"
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    if str(user.get("role") or "").strip().lower() != "admin":
        return False, {}, "账号可登录，但无主站管理权限"
    access_token = str(data.get("access_token") or "").strip()
    refresh_token = str(data.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        return False, {}, "sub2api 主站登录没有返回完整 token"
    expires_in = max(0, int(data.get("expires_in") or 0))
    return True, {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_expires_at": int(time.time()) + expires_in,
    }, None
```

- [ ] **Step 4: Persist and ensure sessions with one refresh and one relogin**

Add:

```python
def _admin_sub2api_session_lock(site_id: int) -> threading.RLock:
    with ADMIN_SUB2API_SESSION_LOCKS_GUARD:
        return ADMIN_SUB2API_SESSION_LOCKS.setdefault(int(site_id), threading.RLock())


def _persist_sub2api_admin_auth(site_id: int, auth: Dict[str, Any]) -> None:
    db_execute(
        """
        UPDATE admin_sites
        SET sub2api_access_token = ?, sub2api_refresh_token = ?,
            sub2api_access_expires_at = ?, browser_login_last_error = NULL,
            browser_login_last_check_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            str(auth.get("access_token") or ""),
            str(auth.get("refresh_token") or ""),
            int(auth.get("access_expires_at") or 0),
            utc_now_iso(),
            utc_now_iso(),
            int(site_id),
        ),
    )


def _persist_sub2api_admin_error(site_id: int, message: str) -> None:
    db_execute(
        """
        UPDATE admin_sites
        SET browser_login_last_error = ?, browser_login_last_check_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (str(message), utc_now_iso(), utc_now_iso(), int(site_id)),
    )


def ensure_sub2api_admin_session(
    site: Dict[str, Any], force_refresh: bool = False
) -> Tuple[bool, str, Optional[str]]:
    site_id = int(site.get("id") or 0)
    with _admin_sub2api_session_lock(site_id):
        current = db_query_one("SELECT * FROM admin_sites WHERE id = ?", (site_id,)) or dict(site)
        access = str(current.get("sub2api_access_token") or "").strip()
        refresh = str(current.get("sub2api_refresh_token") or "").strip()
        expires_at = int(current.get("sub2api_access_expires_at") or 0)
        if access and not force_refresh and expires_at > int(time.time()) + ADMIN_SUB2API_EXPIRY_SKEW_SECONDS:
            return True, access, None
        refresh_error: Optional[str] = None
        if refresh:
            refreshed, data, refresh_error = sub2api_admin_refresh_token(current["base_url"], refresh)
            if refreshed:
                auth = {
                    "access_token": str(data.get("access_token") or ""),
                    "refresh_token": str(data.get("refresh_token") or refresh),
                    "access_expires_at": int(time.time()) + max(0, int(data.get("expires_in") or 0)),
                }
                if auth["access_token"]:
                    _persist_sub2api_admin_auth(site_id, auth)
                    return True, auth["access_token"], None
        logged_in, auth, login_error = sub2api_admin_login(
            current["base_url"],
            str(current.get("login_username") or ""),
            str(current.get("login_password") or ""),
        )
        if not logged_in:
            message = login_error or refresh_error or "sub2api 主站登录失败"
            _persist_sub2api_admin_error(site_id, message)
            return False, "", message
        _persist_sub2api_admin_auth(site_id, auth)
        return True, str(auth["access_token"]), None
```

- [ ] **Step 5: Run the authentication tests**

Run the Step 2 command again. Expected: four tests pass.

- [ ] **Step 6: Commit when the staged diff contains only this task**

```bash
git add app.py tests/test_sub2api_admin_sites.py
git commit -m "feat: manage sub2api admin sessions"
```

### Task 3: Make main-site CRUD platform-aware

**Files:**
- Modify: `app.py:1775-1881`
- Modify: `app.py:5465-5473`
- Modify: `app.py:5595-5613`
- Modify: `tests/test_sub2api_admin_sites.py`

- [ ] **Step 1: Add failing create, update, and connection-test cases**

Append tests that assert:

```python
    def test_create_sub2api_admin_site_logs_in_before_insert(self):
        auth = {"access_token": "access", "refresh_token": "refresh", "access_expires_at": 9999999999}
        body = {
            "name": "sub main",
            "platform": "sub2api",
            "base_url": "https://sub.example",
            "login_username": "admin@example.com",
            "login_password": "password",
        }
        with patch.object(app, "sub2api_admin_login", return_value=(True, auth, None)) as login, \
             patch.object(app, "db_execute", return_value=17) as execute:
            ok, site_id, error = app.create_admin_site(body)
        self.assertTrue(ok)
        self.assertEqual(site_id, 17)
        self.assertIsNone(error)
        login.assert_called_once()
        sql, params = execute.call_args.args
        self.assertIn("platform", sql)
        self.assertIn("sub2api", params)
        self.assertIn("refresh", params)

    def test_update_rejects_platform_change(self):
        existing = {"id": 3, "platform": "newapi", "name": "main", "base_url": "https://new.example"}
        with patch.object(app, "db_query_one", return_value=existing), patch.object(app, "db_execute") as execute:
            ok, error = app.update_admin_site(3, {"platform": "sub2api"})
        self.assertFalse(ok)
        self.assertIn("不可修改", error)
        execute.assert_not_called()

    def test_connection_test_reports_sub2api_channel_count(self):
        body = {
            "platform": "sub2api",
            "base_url": "https://sub.example",
            "login_username": "admin@example.com",
            "login_password": "password",
        }
        auth = {"access_token": "access", "refresh_token": "refresh", "access_expires_at": 9999999999}
        with patch.object(app, "sub2api_admin_login", return_value=(True, auth, None)), \
             patch.object(app, "fetch_sub2api_admin_channels_by_token", return_value=(True, [{"id": 1}, {"id": 2}], None)):
            ok, payload, error = app.test_admin_site_connection(body)
        self.assertTrue(ok)
        self.assertEqual(payload["channels_count"], 2)
        self.assertEqual(payload["platform"], "sub2api")
        self.assertIsNone(error)
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
python3 -m unittest tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_create_sub2api_admin_site_logs_in_before_insert tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_update_rejects_platform_change tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_connection_test_reports_sub2api_channel_count -v
```

Expected: failures because CRUD is still NewAPI-only and the test dispatcher is missing.

- [ ] **Step 3: Branch validation and persistence by platform**

Update `create_admin_site()` so:

```python
platform = str(body.get("platform") or "newapi").strip().lower()
if platform not in {"newapi", "sub2api"}:
    return False, None, "主站平台只支持 NewAPI 或 sub2api"
base_url, base_url_error = validate_admin_site_base_url(str(body.get("base_url") or ""))
if base_url_error:
    return False, None, base_url_error
```

For `newapi`, retain the current required `access_token + access_user_id` behavior. For `sub2api`, require `login_username + login_password`, call `sub2api_admin_login`, and insert the platform, credentials, and returned session in one `INSERT`.

Update `update_admin_site()` to reject any requested platform different from the stored platform. For a sub2api row, validate a changed Base URL, email, or non-empty password with `sub2api_admin_login` before writing it, then replace the stored sub2api session atomically. For a NewAPI row, preserve the current edit semantics.

Implement connection testing:

```python
def test_admin_site_connection(
    body: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    saved = None
    admin_site_id = int(body.get("admin_site_id") or 0)
    if admin_site_id:
        saved = db_query_one("SELECT * FROM admin_sites WHERE id = ?", (admin_site_id,))
        if not saved:
            return False, {}, "管理站点不存在"
    platform = str(body.get("platform") or (saved or {}).get("platform") or "newapi").strip().lower()
    base_url = str(body.get("base_url") or (saved or {}).get("base_url") or "")
    if platform == "newapi":
        access_token = str(body.get("access_token") or (saved or {}).get("access_token") or "")
        access_user_id = str(body.get("access_user_id") or (saved or {}).get("access_user_id") or "")
        ok, payload, error = fetch_newapi_groups_with_access_token(
            base_url,
            access_token,
            access_user_id,
        )
        groups = parse_groups_payload(payload) if ok else {}
        return ok, {"platform": "newapi", "groups_count": len(groups)}, error
    if platform != "sub2api":
        return False, {}, "主站平台无效"
    ok, auth, error = sub2api_admin_login(
        base_url,
        str(body.get("login_username") or (saved or {}).get("login_username") or ""),
        str(body.get("login_password") or (saved or {}).get("login_password") or ""),
    )
    if not ok:
        return False, {}, error
    channels_ok, channels, channels_error = fetch_sub2api_admin_channels_by_token(
        base_url, str(auth["access_token"])
    )
    if not channels_ok:
        return False, {}, channels_error
    return True, {"platform": "sub2api", "channels_count": len(channels)}, None
```

- [ ] **Step 4: Add `POST /api/admin/sites/test` and map immutable-platform conflicts**

In `do_POST`, before `POST /api/admin/sites`, read the body, call `test_admin_site_connection`, and return `200` on success or `400/502` with the sanitized error. In `do_PUT`, return `409` when `update_admin_site` reports a platform-change conflict; keep other validation errors at `400`.

- [ ] **Step 5: Run the focused CRUD tests**

Run the Step 2 command again. Expected: all three tests pass.

- [ ] **Step 6: Commit the platform-aware CRUD**

```bash
git add app.py tests/test_sub2api_admin_sites.py
git commit -m "feat: add sub2api main site credentials"
```

### Task 4: Implement the sub2api channel and group adapter

**Files:**
- Modify: `app.py:1920-2380`
- Modify: `app.py:3120-3200`
- Modify: `tests/test_sub2api_admin_sites.py`

- [ ] **Step 1: Add failing pagination, normalization, update, and exclusion tests**

Append:

```python
    def test_sub2api_admin_channel_adapter_reads_all_pages_and_never_accounts(self):
        responses = [
            (True, {"code": 0, "data": {"items": [{"id": 1}], "total": 2, "page": 1, "page_size": 1}}, None),
            (True, {"code": 0, "data": {"items": [{"id": 2}], "total": 2, "page": 2, "page_size": 1}}, None),
        ]
        with patch.object(app, "admin_request_json", side_effect=responses) as request:
            ok, channels, error = app.fetch_sub2api_admin_channels_by_token(
                "https://sub.example", "access", page_size=1
            )
        self.assertTrue(ok)
        self.assertEqual([item["id"] for item in channels], [1, 2])
        self.assertIsNone(error)
        urls = [call.args[0] for call in request.call_args_list]
        self.assertTrue(all("/api/v1/admin/channels" in url for url in urls))
        self.assertTrue(all("/api/v1/admin/accounts" not in url for url in urls))

    def test_normalized_sub2api_channel_contains_group_rates_and_pricing(self):
        channel = {
            "id": 7,
            "name": "Claude",
            "status": "active",
            "group_ids": [2],
            "model_pricing": [{"platform": "anthropic", "models": ["claude-sonnet-4"], "billing_mode": "token"}],
        }
        groups = {2: {"id": 2, "name": "高级组", "rate_multiplier": 0.8, "platform": "anthropic", "status": "active"}}
        result = app.normalize_sub2api_admin_channel(channel, groups)
        self.assertEqual(result["source_platform"], "sub2api")
        self.assertEqual(result["normalized_status"], "active")
        self.assertEqual(result["groups"][0]["rate_multiplier"], 0.8)
        self.assertEqual(result["model_pricing"][0]["models"], ["claude-sonnet-4"])

    def test_sub2api_channel_update_rejects_unknown_fields_and_preserves_empty_values(self):
        site = {"id": 5, "platform": "sub2api", "base_url": "https://sub.example"}
        with patch.object(app, "sub2api_admin_request", return_value=(True, {"code": 0, "data": {"id": 7}}, None)) as request:
            ok, payload, error = app.update_sub2api_admin_channel(site, 7, {"group_ids": [], "model_mapping": {}})
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(request.call_args.kwargs["payload"]["group_ids"], [])
        self.assertEqual(request.call_args.kwargs["payload"]["model_mapping"], {})

        ok, payload, error = app.update_sub2api_admin_channel(site, 7, {"password": "leak"})
        self.assertFalse(ok)
        self.assertEqual(payload, {})
        self.assertIn("password", error)
```

- [ ] **Step 2: Run the adapter tests and verify missing functions fail**

Run:

```bash
python3 -m unittest tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_sub2api_admin_channel_adapter_reads_all_pages_and_never_accounts tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_normalized_sub2api_channel_contains_group_rates_and_pricing tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_sub2api_channel_update_rejects_unknown_fields_and_preserves_empty_values -v
```

Expected: errors for missing adapter functions.

- [ ] **Step 3: Add authenticated request retry and channel pagination**

Implement the authenticated request wrapper:

```python
def sub2api_admin_request(
    site: Dict[str, Any],
    path: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    session_ok, token, session_error = ensure_sub2api_admin_session(site)
    if not session_ok:
        return False, {}, session_error
    url = f"{normalize_base_url(site['base_url'])}{path}"
    ok, response, error = admin_request_json(
        url,
        headers=sub2api_token_headers(token),
        payload=payload,
        method=method,
    )
    if ok or not is_sub2api_auth_error(response, error):
        return ok, response if isinstance(response, dict) else {}, error
    session_ok, token, session_error = ensure_sub2api_admin_session(
        site, force_refresh=True
    )
    if not session_ok:
        return False, {}, session_error
    ok, response, error = admin_request_json(
        url,
        headers=sub2api_token_headers(token),
        payload=payload,
        method=method,
    )
    return ok, response if isinstance(response, dict) else {}, error
```

Implement `fetch_sub2api_admin_channels_by_token()` against:

```text
/api/v1/admin/channels?page=<n>&page_size=<size>&search=<encoded>
```

Use this pagination shape, stop when collected items reach `total` or a short page is returned, and return an explicit truncation error after 100 pages:

```python
def fetch_sub2api_admin_channels_by_token(
    base_url: str,
    access_token: str,
    keyword: str = "",
    page_size: int = 100,
    max_pages: int = 100,
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    items: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        query = f"page={page}&page_size={int(page_size)}"
        if str(keyword or "").strip():
            query += f"&search={quote(str(keyword).strip())}"
        ok, payload, error = admin_request_json(
            f"{normalize_base_url(base_url)}/api/v1/admin/channels?{query}",
            headers=sub2api_token_headers(access_token),
        )
        if not ok:
            return False, [], error or "读取 sub2api 渠道失败"
        success, data, message = unwrap_sub2api_response(payload)
        if not success or not isinstance(data, dict):
            return False, [], message or "sub2api 渠道响应异常"
        page_items = [value for value in data.get("items") or [] if isinstance(value, dict)]
        items.extend(page_items)
        total = int(data.get("total") or 0)
        if (total and len(items) >= total) or len(page_items) < page_size:
            return True, items, None
    return False, [], "sub2api 渠道超过最大分页页数，拒绝返回截断数据"


def fetch_sub2api_admin_site_channels(
    site: Dict[str, Any], keyword: str = "", page_size: int = 100, max_pages: int = 100
) -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    items: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        query = f"page={page}&page_size={int(page_size)}"
        if str(keyword or "").strip():
            query += f"&search={quote(str(keyword).strip())}"
        ok, payload, error = sub2api_admin_request(
            site, f"/api/v1/admin/channels?{query}"
        )
        if not ok:
            return False, [], {}, error or "读取 sub2api 渠道失败"
        success, data, message = unwrap_sub2api_response(payload)
        if not success or not isinstance(data, dict):
            return False, [], {}, message or "sub2api 渠道响应异常"
        page_items = [value for value in data.get("items") or [] if isinstance(value, dict)]
        items.extend(page_items)
        total = int(data.get("total") or 0)
        if (total and len(items) >= total) or len(page_items) < page_size:
            return True, items, {"total": total or len(items)}, None
    return False, [], {}, "sub2api 渠道超过最大分页页数，拒绝返回截断数据"
```

- [ ] **Step 4: Add group lookup and channel normalization**

Fetch `/api/v1/admin/groups/all`, unwrap `data`, expose the existing record-shaped API contract, and index groups by integer ID:

```python
def fetch_sub2api_admin_groups(
    site: Dict[str, Any]
) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
    ok, payload, error = sub2api_admin_request(site, "/api/v1/admin/groups/all")
    if not ok:
        return False, [], error or "读取 sub2api 分组失败"
    success, data, message = unwrap_sub2api_response(payload)
    if not success or not isinstance(data, list):
        return False, [], message or "sub2api 分组响应异常"
    return True, [value for value in data if isinstance(value, dict)], None


def sub2api_admin_groups_payload(groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    data: Dict[str, Dict[str, Any]] = {}
    for item in groups:
        name = str(item.get("name") or f"#{item.get('id')}")
        data[name] = {
            "id": int(item.get("id") or 0),
            "name": name,
            "ratio": item.get("rate_multiplier"),
            "rate_multiplier": item.get("rate_multiplier"),
            "ratio_type": "number",
            "desc": item.get("description") or "",
            "platform": item.get("platform") or "",
            "status": item.get("status") or "",
        }
    return {"success": True, "data": data}
```

Then normalize each channel with:

```python
def normalize_sub2api_admin_channel(
    channel: Dict[str, Any], groups_by_id: Dict[int, Dict[str, Any]]
) -> Dict[str, Any]:
    normalized = dict(channel)
    status = str(channel.get("status") or "disabled").strip().lower()
    group_ids = [int(value) for value in channel.get("group_ids") or []]
    normalized.update({
        "source_platform": "sub2api",
        "normalized_status": "active" if status == "active" else "disabled",
        "group_ids": group_ids,
        "groups": [
            {
                "id": group_id,
                "name": groups_by_id[group_id].get("name") or f"#{group_id}",
                "platform": groups_by_id[group_id].get("platform") or "",
                "status": groups_by_id[group_id].get("status") or "",
                "rate_multiplier": groups_by_id[group_id].get("rate_multiplier"),
            }
            for group_id in group_ids
            if group_id in groups_by_id
        ],
        "model_pricing": list(channel.get("model_pricing") or []),
        "model_mapping": dict(channel.get("model_mapping") or {}),
        "capabilities": {"edit": True, "toggle": True, "create": False, "delete": False},
    })
    return normalized
```

- [ ] **Step 5: Add detail and allowlisted update adapters**

Define:

```python
SUB2API_ADMIN_CHANNEL_UPDATE_FIELDS = {
    "name", "description", "status", "group_ids", "model_pricing",
    "model_mapping", "billing_model_source", "restrict_models", "features",
    "features_config", "apply_pricing_to_account_stats",
    "account_stats_pricing_rules",
}
```

`update_sub2api_admin_channel` must reject every unknown key before making a request, preserve empty lists/objects/strings, validate `status` as `active|disabled`, and call `PUT /api/v1/admin/channels/:id` with exactly the allowlisted patch.

- [ ] **Step 6: Run the adapter tests**

Run the Step 2 command again. Expected: all three tests pass.

- [ ] **Step 7: Commit the adapter task**

```bash
git add app.py tests/test_sub2api_admin_sites.py
git commit -m "feat: adapt sub2api main site channels"
```

### Task 5: Dispatch unified HTTP routes and enforce method boundaries

**Files:**
- Modify: `app.py:1419-1440`
- Modify: `app.py:5238-5317`
- Modify: `app.py:5465-5589`
- Modify: `app.py:5595-5648`
- Modify: `app.py:5804-5838`
- Modify: `tests/test_sub2api_admin_sites.py`

- [ ] **Step 1: Add failing platform-dispatch tests**

Add unit tests for these public helpers:

```python
    def test_unified_channel_dispatch_uses_sub2api_adapter(self):
        site = {"id": 5, "platform": "sub2api"}
        with patch.object(app, "fetch_sub2api_admin_site_channels", return_value=(True, [{"id": 1}], {"total": 1}, None)) as sub, \
             patch.object(app, "fetch_all_newapi_channels") as newapi:
            result = app.fetch_admin_site_channels(site, "claude")
        self.assertEqual(result[1][0]["id"], 1)
        sub.assert_called_once_with(site, "claude")
        newapi.assert_not_called()

    def test_sub2api_create_and_delete_capabilities_are_false(self):
        site = {"platform": "sub2api"}
        self.assertFalse(app.admin_site_capabilities(site)["create_channel"])
        self.assertFalse(app.admin_site_capabilities(site)["delete_channel"])

    def test_newapi_dispatch_keeps_existing_adapter(self):
        site = {"id": 2, "platform": "newapi"}
        with patch.object(app, "fetch_all_newapi_channels", return_value=(True, [{"id": 9}], None)) as newapi:
            ok, items, meta, error = app.fetch_admin_site_channels(site, "")
        self.assertTrue(ok)
        self.assertEqual(items[0]["id"], 9)
        self.assertEqual(meta["total"], 1)
        self.assertIsNone(error)
        newapi.assert_called_once_with(site)
```

- [ ] **Step 2: Run the dispatch tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_unified_channel_dispatch_uses_sub2api_adapter tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_sub2api_create_and_delete_capabilities_are_false tests.test_sub2api_admin_sites.Sub2ApiAdminSiteTests.test_newapi_dispatch_keeps_existing_adapter -v
```

Expected: missing `fetch_admin_site_channels` causes failure.

- [ ] **Step 3: Add unified dispatch helpers**

Implement `fetch_admin_site_channels`, `fetch_admin_site_groups`, `fetch_admin_site_channel_detail`, and `update_admin_site_channel`. NewAPI branches call the current functions unchanged; sub2api branches call Task 4 adapters.

Change `get_admin_site_or_404` validation:

```python
platform = admin_site_platform(site)
if platform == "newapi" and not (site.get("access_token") and site.get("access_user_id")):
    return None, {"success": False, "message": "该 NewAPI 主站未配置管理员系统访问令牌和用户 ID"}, 400
if platform == "sub2api" and not (site.get("login_username") and site.get("login_password")):
    return None, {"success": False, "message": "该 sub2api 主站未配置管理员邮箱和密码"}, 400
```

- [ ] **Step 4: Route all reads and updates through dispatch helpers**

Replace direct NewAPI calls in `do_GET` and `do_PUT`. The sub2api `/groups` branch returns `sub2api_admin_groups_payload(groups)` so the existing `Record<string, GroupItem>` API shape remains stable. Only expose `channel-mappings`, key verification, and channel test for NewAPI. A sub2api request to those platform-specific routes returns `405` with a platform-specific message.

For `POST /api/admin/sites/:id/channels` and `DELETE /api/admin/sites/:id/channels/:cid`, check capabilities before reading the body or calling an upstream API:

```python
if not admin_site_capabilities(site)["create_channel"]:
    return json_response(self, {"success": False, "message": "sub2api 主站不允许在本系统新建渠道"}, 405)
```

and:

```python
if not admin_site_capabilities(site)["delete_channel"]:
    return json_response(self, {"success": False, "message": "sub2api 主站不允许在本系统删除渠道"}, 405)
```

- [ ] **Step 5: Run backend main-site tests**

Run:

```bash
python3 -m unittest tests.test_sub2api_admin_sites tests.test_newapi_channel_key_matching tests.test_sub2api_channel_key_matching -v
```

Expected: all tests pass and no test URL contains `/api/v1/admin/accounts`.

- [ ] **Step 6: Commit route dispatch when safe**

```bash
git add app.py tests/test_sub2api_admin_sites.py
git commit -m "feat: dispatch main site routes by platform"
```

### Task 6: Add frontend contracts and pure sub2api channel helpers

**Files:**
- Modify: `apps/web/src/lib/types.ts:140-260`
- Modify: `apps/web/src/lib/api.ts:149-239`
- Create: `apps/web/src/lib/sub2apiChannel.ts`
- Create: `tests/web/sub2api-main-site.test.mjs`

- [ ] **Step 1: Write failing frontend contract tests**

Create `tests/web/sub2api-main-site.test.mjs`:

```javascript
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const apiUrl = new URL("../../apps/web/src/lib/api.ts", import.meta.url);
const typesUrl = new URL("../../apps/web/src/lib/types.ts", import.meta.url);
const helpersUrl = new URL("../../apps/web/src/lib/sub2apiChannel.ts", import.meta.url);

test("main-site API exposes the unified connection test", async () => {
  const source = await readFile(apiUrl, "utf8");
  assert.match(source, /testAdminSite/);
  assert.match(source, /\/api\/admin\/sites\/test/);
});

test("main-site contracts include capabilities and sub2api pricing", async () => {
  const source = await readFile(typesUrl, "utf8");
  assert.match(source, /AdminSiteCapabilities/);
  assert.match(source, /Sub2ApiModelPricing/);
  assert.match(source, /source_platform/);
  assert.match(source, /normalized_status/);
});

test("sub2api helper preserves explicit empty values", async () => {
  const helpers = await import(helpersUrl);
  const original = { id: 1, name: "A", group_ids: [1], model_mapping: { anthropic: { a: "b" } } };
  const edited = { ...original, group_ids: [], model_mapping: {} };
  assert.deepEqual(helpers.buildSub2ApiChannelPatch(original, edited), {
    group_ids: [],
    model_mapping: {},
  });
});

test("status normalization supports both main-site platforms", async () => {
  const helpers = await import(helpersUrl);
  assert.equal(helpers.normalizedChannelStatus({ status: 1 }), "active");
  assert.equal(helpers.normalizedChannelStatus({ status: 2 }), "disabled");
  assert.equal(helpers.normalizedChannelStatus({ status: 3 }), "error");
  assert.equal(helpers.normalizedChannelStatus({ status: "active" }), "active");
  assert.equal(helpers.normalizedChannelStatus({ status: "disabled" }), "disabled");
});
```

- [ ] **Step 2: Run the frontend tests and verify missing modules fail**

Run:

```bash
node --test tests/web/sub2api-main-site.test.mjs
```

Expected: failures because the helper and new types do not exist.

- [ ] **Step 3: Add discriminated channel and capability types**

In `types.ts`, add:

```typescript
export type AdminSiteCapabilities = {
  list_channels: boolean;
  read_channel_detail: boolean;
  edit_channel: boolean;
  toggle_channel: boolean;
  create_channel: boolean;
  delete_channel: boolean;
  channel_key: boolean;
  channel_priority: boolean;
  channel_weight: boolean;
  group_rates: boolean;
  model_pricing: boolean;
};

export type Sub2ApiGroupRef = {
  id: number;
  name: string;
  platform?: string;
  status?: string;
  rate_multiplier?: number | null;
};

export type Sub2ApiPricingInterval = {
  id?: number;
  min_tokens: number;
  max_tokens?: number | null;
  tier_label?: string;
  input_price?: number | null;
  output_price?: number | null;
  cache_write_price?: number | null;
  cache_read_price?: number | null;
  per_request_price?: number | null;
  sort_order?: number;
};

export type Sub2ApiModelPricing = {
  id?: number;
  platform: string;
  models: string[];
  billing_mode: "token" | "per_request" | "image" | string;
  input_price?: number | null;
  output_price?: number | null;
  cache_write_price?: number | null;
  cache_read_price?: number | null;
  image_input_price?: number | null;
  image_output_price?: number | null;
  per_request_price?: number | null;
  intervals: Sub2ApiPricingInterval[];
};

export type Sub2ApiAccountStatsPricingRule = {
  id?: number;
  name: string;
  group_ids: number[];
  account_ids: number[];
  pricing: Sub2ApiModelPricing[];
};
```

Extend the existing `GroupItem` with `id?: number`, `name?: string`, and `rate_multiplier?: number | null` so the record-shaped `/groups` response keeps both its legacy ratio fields and the sub2api identity fields.

Change `Channel.status` to `number | string`, `model_mapping` to `string | Record<string, Record<string, string>>`, and add the normalized sub2api fields from the design. Add `platform`, `capabilities`, `has_sub2api_session`, `login_last_error`, and `login_last_check_at` to `AdminSite`; add `platform` to `AdminSiteFormPayload`.

- [ ] **Step 4: Add pure status and patch helpers**

Create `sub2apiChannel.ts` with deep comparison based on stable JSON serialization and these exports:

```typescript
import type { Channel } from "./types";

export function isSub2ApiChannel(channel: Channel): boolean {
  return channel.source_platform === "sub2api";
}

export function normalizedChannelStatus(
  channel: Pick<Channel, "status" | "normalized_status">,
): "active" | "disabled" | "error" {
  if (channel.normalized_status === "active") return "active";
  if (channel.normalized_status === "disabled") return "disabled";
  if (channel.normalized_status === "error") return "error";
  if (channel.status === 1 || channel.status === "active") return "active";
  if (channel.status === 2 || channel.status === "disabled") return "disabled";
  return "error";
}

const editableFields = [
  "name", "description", "status", "group_ids", "model_pricing",
  "model_mapping", "billing_model_source", "restrict_models", "features",
  "features_config", "apply_pricing_to_account_stats",
  "account_stats_pricing_rules",
] as const;

export function buildSub2ApiChannelPatch(
  original: Channel,
  edited: Channel,
): Partial<Channel> {
  const patch: Partial<Channel> = {};
  for (const field of editableFields) {
    if (JSON.stringify(original[field]) !== JSON.stringify(edited[field])) {
      (patch as Record<string, unknown>)[field] = edited[field];
    }
  }
  return patch;
}
```

- [ ] **Step 5: Add the unified API call**

Add:

```typescript
testAdminSite: (payload: AdminSiteFormPayload & { admin_site_id?: number }) =>
  request<{ success: boolean; platform?: Platform; groups_count?: number; channels_count?: number; message?: string }>(
    "/api/admin/sites/test",
    { method: "POST", body: JSON.stringify(payload) },
  ),
```

Keep existing API methods for NewAPI compatibility and widen `updateChannel` to accept the updated `Partial<Channel>`.

- [ ] **Step 6: Run tests and TypeScript build**

```bash
node --test tests/web/sub2api-main-site.test.mjs
npm --prefix apps/web run build
```

Expected: tests pass and the production build succeeds.

- [ ] **Step 7: Commit frontend contracts when safe**

```bash
git add apps/web/src/lib/types.ts apps/web/src/lib/api.ts apps/web/src/lib/sub2apiChannel.ts tests/web/sub2api-main-site.test.mjs
git commit -m "feat: define sub2api main site contracts"
```

### Task 7: Make the main-site form platform-aware

**Files:**
- Modify: `apps/web/src/components/AdminSiteFormDialog.tsx`
- Modify: `tests/web/sub2api-main-site.test.mjs`

- [ ] **Step 1: Add source-level form behavior tests**

Append:

```javascript
const adminFormUrl = new URL(
  "../../apps/web/src/components/AdminSiteFormDialog.tsx",
  import.meta.url,
);

test("admin-site form selects a platform only during creation", async () => {
  const source = await readFile(adminFormUrl, "utf8");
  assert.match(source, /option value="newapi"/);
  assert.match(source, /option value="sub2api"/);
  assert.match(source, /disabled=\{editing\}/);
});

test("sub2api form uses administrator email and password without NewAPI fields", async () => {
  const source = await readFile(adminFormUrl, "utf8");
  assert.match(source, /sub2api 管理员邮箱/);
  assert.match(source, /sub2api 管理员密码/);
  assert.match(source, /api\.testAdminSite\(/);
  assert.match(source, /admin_site_id: site\?\.id/);
  assert.match(source, /form\.platform === "sub2api"/);
});
```

- [ ] **Step 2: Run and verify the form tests fail**

```bash
node --test tests/web/sub2api-main-site.test.mjs
```

Expected: the new form assertions fail.

- [ ] **Step 3: Branch validation and fields by platform**

Add `platform: "newapi"` to the empty form. When editing, initialize from `site.platform`; disable the platform select for an existing site.

NewAPI validation remains `access_token + access_user_id` for a new site. sub2api validation requires `login_username + login_password` for a new site and allows an empty password during edit when `site.has_login_password` is true.

Replace `api.checkLogin` with `api.testAdminSite({ ...payload, admin_site_id: site?.id })`. The backend merges saved credentials for an existing site, so an empty password or token remains “keep current” during connection testing. Build the payload once and remove irrelevant credentials before sending:

```typescript
const payload: AdminSiteFormPayload = {
  platform: form.platform,
  name: form.name.trim(),
  base_url: form.base_url.trim(),
  access_token: form.platform === "newapi" ? form.access_token.trim() : "",
  access_user_id: form.platform === "newapi" ? form.access_user_id.trim() : "",
  login_username: form.login_username.trim(),
  login_password: form.login_password,
};
```

Render the existing NewAPI token/login/2FA blocks only for NewAPI. For sub2api, render administrator email/password fields and a status line based on `has_sub2api_session` and `login_last_error`. Do not render key verification for sub2api.

- [ ] **Step 4: Run tests and build**

```bash
node --test tests/web/sub2api-main-site.test.mjs
npm --prefix apps/web run build
```

Expected: tests and build pass.

- [ ] **Step 5: Commit the form task**

```bash
git add apps/web/src/components/AdminSiteFormDialog.tsx tests/web/sub2api-main-site.test.mjs
git commit -m "feat: configure sub2api main sites"
```

### Task 8: Build the complete sub2api channel editor

**Files:**
- Create: `apps/web/src/components/Sub2ApiPricingEditor.tsx`
- Create: `apps/web/src/components/Sub2ApiChannelDialog.tsx`
- Modify: `apps/web/src/components/ui.tsx`
- Modify: `tests/web/sub2api-main-site.test.mjs`

- [ ] **Step 1: Add editor structure tests**

Append tests that read both new components and assert:

```javascript
test("sub2api editor exposes every supported configuration section", async () => {
  const source = await readFile(
    new URL("../../apps/web/src/components/Sub2ApiChannelDialog.tsx", import.meta.url),
    "utf8",
  );
  for (const label of ["基本信息", "绑定分组", "模型定价", "模型映射", "高级计费"]) {
    assert.match(source, new RegExp(label));
  }
  assert.match(source, /buildSub2ApiChannelPatch/);
  assert.doesNotMatch(source, /删除渠道|新建渠道/);
});

test("pricing editor supports interval prices", async () => {
  const source = await readFile(
    new URL("../../apps/web/src/components/Sub2ApiPricingEditor.tsx", import.meta.url),
    "utf8",
  );
  assert.match(source, /min_tokens/);
  assert.match(source, /max_tokens/);
  assert.match(source, /cache_write_price/);
  assert.match(source, /cache_read_price/);
  assert.match(source, /per_request_price/);
});
```

- [ ] **Step 2: Run and verify the missing component tests fail**

```bash
node --test tests/web/sub2api-main-site.test.mjs
```

Expected: `ENOENT` for the editor files.

- [ ] **Step 3: Add a reusable tab control and pricing editor**

Add a compact `Tabs` component to `ui.tsx` with `role="tablist"`, `role="tab"`, `aria-selected`, stable height, and PriceAI tokens.

`Sub2ApiPricingEditor` receives `value: Sub2ApiModelPricing[]` and `onChange`. It must support:

- add/remove pricing rows;
- platform text/select value;
- comma/newline model list normalized to unique trimmed strings;
- billing mode segmented select;
- nullable numeric prices for input, output, cache write/read, image input/output, and per request;
- add/remove intervals with min/max tokens, label, the same token prices, and sort order.

Use Lucide `Plus` and `Trash2` icon buttons with `aria-label` and `title`; do not use text-only rounded buttons for these tools.

- [ ] **Step 4: Implement the tabbed channel dialog**

`Sub2ApiChannelDialog` receives:

```typescript
{
  open: boolean;
  channel: Channel | null;
  groups: Sub2ApiGroupRef[];
  onClose: () => void;
  onSubmit: (patch: Partial<Channel>) => Promise<void>;
}
```

The dialog clones the original channel on open, keeps active tab and errors on failed save, and submits `buildSub2ApiChannelPatch(original, edited)`. Reject an empty patch with “没有需要保存的修改”.

Implement all five tabs with structured controls:

- Basic: name, description, status switch.
- Groups: checkboxes keyed by group ID with name/platform/rate badges.
- Pricing: `Sub2ApiPricingEditor`.
- Mapping: repeatable platform/source/target rows converted to `Record<string, Record<string,string>>`.
- Advanced: billing source select, restrict-model and apply-pricing toggles, features input, JSON `features_config`, and repeatable account-stat rules with name, group IDs, account IDs, and nested `Sub2ApiPricingEditor`.

Parse JSON on blur/save and keep an inline field error when `features_config` is not an object. Preserve empty arrays and objects.

- [ ] **Step 5: Run editor tests and production build**

```bash
node --test tests/web/sub2api-main-site.test.mjs
npm --prefix apps/web run build
```

Expected: tests pass and TypeScript reports no editor type errors.

- [ ] **Step 6: Commit the editor task**

```bash
git add apps/web/src/components/Sub2ApiPricingEditor.tsx apps/web/src/components/Sub2ApiChannelDialog.tsx apps/web/src/components/ui.tsx tests/web/sub2api-main-site.test.mjs
git commit -m "feat: edit sub2api channel configuration"
```

### Task 9: Render and operate sub2api channels on the main-site page

**Files:**
- Modify: `apps/web/src/pages/ChannelsPage.tsx`
- Modify: `tests/web/main-site-channel-boundary.test.mjs`
- Modify: `tests/web/sub2api-main-site.test.mjs`

- [ ] **Step 1: Adjust NewAPI boundary tests and add sub2api page tests**

Replace the global assertion `assert.doesNotMatch(source, /ChannelFormDialog/)` with assertions that NewAPI still opens only `ChannelPriorityDialog` while sub2api opens `Sub2ApiChannelDialog`.

Append:

```javascript
const channelsPageUrl = new URL(
  "../../apps/web/src/pages/ChannelsPage.tsx",
  import.meta.url,
);

test("main-site page renders sub2api channels by capabilities", async () => {
  const source = await readFile(channelsPageUrl, "utf8");
  assert.match(source, /currentAdminSite\?\.platform === "sub2api"/);
  assert.match(source, /Sub2ApiChannelDialog/);
  assert.match(source, /模型定价/);
  assert.match(source, /分组倍率/);
  assert.match(source, /toggle_channel/);
});

test("sub2api actions never expose create or delete", async () => {
  const source = await readFile(channelsPageUrl, "utf8");
  assert.doesNotMatch(source, /api\.createChannel\(/);
  assert.doesNotMatch(source, /api\.deleteChannel\(/);
  assert.doesNotMatch(source, /添加 sub2api 渠道|删除 sub2api 渠道/);
});
```

- [ ] **Step 2: Run the page tests and verify failure**

```bash
node --test tests/web/main-site-channel-boundary.test.mjs tests/web/sub2api-main-site.test.mjs
```

Expected: sub2api page assertions fail.

- [ ] **Step 3: Split data loading by platform capabilities**

For NewAPI, retain the current parallel load of channels, groups, and channel mappings. For sub2api, load only channels and groups; do not request `channel-mappings`, channel detail/key, or match endpoints.

On refresh failure, keep the previous channel/group state when the selected site has not changed and show a sticky stale-data warning. On first load or site change, show the full error state.

- [ ] **Step 4: Render a dedicated sub2api table**

Keep the current NewAPI table unchanged. For sub2api, render stable columns:

- channel name/ID/description;
- status badge;
- group badges;
- group-rate badges with `tabular-nums`;
- model count/platform summary;
- billing mode/source;
- edit, enable/disable, and refresh actions.

Use `Pencil`, `Power`, `PowerOff`, and `RefreshCw` icons from Lucide. Every icon action has `title` and `aria-label`. Do not render key, weight, priority, or key-match status for sub2api.

- [ ] **Step 5: Wire edit and status updates**

Open `Sub2ApiChannelDialog` only for a sub2api channel. On save call `api.updateChannel(siteId, channel.id, patch)`, retain the dialog on error, and reload channels/groups on success.

Quick enable/disable submits exactly one field:

```typescript
await api.updateChannel(siteId, channel.id, {
  status: normalizedChannelStatus(channel) === "active" ? "disabled" : "active",
});
```

Disable all row actions while that channel is updating and show a success/error toast with the channel name.

- [ ] **Step 6: Run page tests and build**

```bash
node --test tests/web/main-site-channel-boundary.test.mjs tests/web/sub2api-main-site.test.mjs
npm --prefix apps/web run build
```

Expected: tests pass and both platform branches type-check.

- [ ] **Step 7: Commit the main-site page task**

```bash
git add apps/web/src/pages/ChannelsPage.tsx tests/web/main-site-channel-boundary.test.mjs tests/web/sub2api-main-site.test.mjs
git commit -m "feat: monitor sub2api main site channels"
```

### Task 10: Normalize health, update documentation, and run full verification

**Files:**
- Modify: `apps/web/src/components/MainSiteHealthPanel.tsx`
- Modify: `tests/web/sub2api-main-site.test.mjs`
- Modify: `README.md`
- Modify: `docs/product.md`

- [ ] **Step 1: Add failing mixed-health tests**

Append source and helper assertions:

```javascript
const healthPanelUrl = new URL(
  "../../apps/web/src/components/MainSiteHealthPanel.tsx",
  import.meta.url,
);

test("main-site health uses normalized mixed-platform statuses", async () => {
  const source = await readFile(healthPanelUrl, "utf8");
  assert.match(source, /normalizedChannelStatus/);
  assert.match(source, /已停用/);
  assert.match(source, /异常/);
  assert.doesNotMatch(source, /你自己的中转站（NewAPI 后台）/);
});
```

- [ ] **Step 2: Run the test and verify current NewAPI-only wording fails**

```bash
node --test tests/web/sub2api-main-site.test.mjs
```

Expected: the health-panel assertion fails.

- [ ] **Step 3: Normalize health and restrict re-enable actions**

Use `normalizedChannelStatus` for every channel. Aggregate `{total, active, disabled, error}`. Rename KPI labels to “运行中 / 已停用 / 异常”.

Only render the existing NewAPI recovery action when the site is NewAPI and the raw status is `3`. sub2api disabled channels are deliberate configuration and must not appear as automatic failures. A failed site request appears in the failed-site warning and does not add fake channels.

Update all “NewAPI 后台” global copy to “NewAPI / sub2api 主站”.

- [ ] **Step 4: Document the shipped behavior**

Update `README.md` and `docs/product.md` with:

- main sites support both NewAPI and sub2api;
- sub2api main-site authentication is administrator email/password only;
- sub2api reads `/api/v1/admin/channels` and `/api/v1/admin/groups/all`;
- account pool `/api/v1/admin/accounts` is out of scope;
- sub2api channel edit/toggle is supported, create/delete is blocked;
- main-site monitoring remains live/manual and does not create snapshots or notifications.

- [ ] **Step 5: Run all automated verification**

```bash
python3 -m unittest discover -s tests -v
node --test tests/web/*.test.mjs
npm --prefix apps/web run build
```

Expected: all Python and Node tests pass; Vite emits a production bundle with no TypeScript errors.

- [ ] **Step 6: Run non-destructive local API smoke tests**

Start the backend on an unused loopback port with the configured MySQL database, then request:

```bash
curl -fsS http://127.0.0.1:18765/api/overview
curl -fsS http://127.0.0.1:18765/api/sites
curl -fsS http://127.0.0.1:18765/api/admin/sites
```

Expected: all three return JSON without exposing any password or token. Do not create, edit, disable, or delete a real upstream channel during automated smoke testing.

- [ ] **Step 7: Inspect the final diff for secret and scope violations**

```bash
git diff --check
rg -n "api/v1/admin/accounts|sub2api_access_token|sub2api_refresh_token|login_password" app.py apps/web/src tests
```

Expected: `/api/v1/admin/accounts` appears only in explicit negative tests/documentation; backend secret fields appear only in persistence/auth code; frontend never receives or displays saved secret values.

- [ ] **Step 8: Commit documentation and final integration when safe**

```bash
git add apps/web/src/components/MainSiteHealthPanel.tsx tests/web/sub2api-main-site.test.mjs README.md docs/product.md
git commit -m "docs: document sub2api main site support"
```
