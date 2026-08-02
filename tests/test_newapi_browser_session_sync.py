import re
import unittest
from unittest.mock import patch

import app


class NewApiBrowserSessionSyncTests(unittest.TestCase):
    def _completion_request(self, target_kind="site"):
        return {
            "id": "newapi-request",
            "site_id": 7 if target_kind == "site" else None,
            "admin_site_id": 13 if target_kind == "admin_site" else None,
            "platform": "newapi",
            "target_origin": "https://newapi.example",
            "status": "validating",
        }

    def _modern_completion_body(self):
        return {
            "status": "session_found",
            "platform": "newapi",
            "observed_origin": "https://newapi.example",
            "session": {
                "access_token": "dashboard-access",
                "access_user_id": "21",
                "browser_refresh_cookie": "new_api_refresh=refresh-secret",
                "browser_session_id": "session-21",
                "browser_access_expires_at": 4102444800,
            },
        }

    def test_site_schema_adds_newapi_browser_session_columns_incrementally(self):
        self.assertEqual(app.SITES_COLUMN_ADDITIONS["browser_refresh_cookie"], "TEXT")
        self.assertEqual(
            app.SITES_COLUMN_ADDITIONS["browser_session_id"], "VARCHAR(255)"
        )
        self.assertEqual(
            app.SITES_COLUMN_ADDITIONS["browser_access_expires_at"], "BIGINT"
        )
        sites_ddl = next(
            ddl for ddl in app.DDL_STATEMENTS if "CREATE TABLE IF NOT EXISTS sites" in ddl
        )
        self.assertIn("browser_refresh_cookie TEXT", sites_ddl)
        self.assertIn("browser_session_id VARCHAR(255)", sites_ddl)
        self.assertIn("browser_access_expires_at BIGINT", sites_ddl)

    def test_legacy_session_persistence_uses_site_auth_fields_without_modern_state(self):
        session = {
            "access_token": "legacy-system-token",
            "access_user_id": "21",
        }
        with patch.object(app, "db_execute", return_value=1) as execute:
            app.persist_newapi_site_browser_session(7, session)

        sql, params = execute.call_args.args
        self.assertIn("UPDATE sites", sql)
        self.assertIn("auth_mode = 'browser'", sql)
        self.assertRegex(sql, r"(?<!browser_)access_token\s*=\s*\?")
        self.assertIn("access_user_id = ?", sql)
        self.assertIn("browser_refresh_cookie = ?", sql)
        self.assertEqual(params[0], "legacy-system-token")
        self.assertEqual(params[1], "21")
        self.assertEqual(params[2:5], (None, None, 0))

    def test_modern_session_persistence_keeps_refresh_session_and_expiry(self):
        session = {
            "access_token": "dashboard-access-token",
            "access_user_id": "21",
            "browser_refresh_cookie": "new_api_refresh=rotating-cookie",
            "browser_session_id": "session-21",
            "browser_access_expires_at": 4102444800,
        }
        with patch.object(app, "db_execute", return_value=1) as execute:
            app.persist_newapi_site_browser_session(7, session)

        sql, params = execute.call_args.args
        self.assertIn("browser_refresh_cookie = ?", sql)
        self.assertIn("browser_session_id = ?", sql)
        self.assertIn("browser_access_expires_at = ?", sql)
        self.assertEqual(params[2], "new_api_refresh=rotating-cookie")
        self.assertEqual(params[3], "session-21")
        self.assertEqual(params[4], 4102444800)

    def test_validation_requires_user_id_then_checks_self_and_groups(self):
        missing_ok, _payload, missing_error = app.validate_newapi_site_browser_session(
            "https://newapi.example", {"access_token": "access-token"}
        )
        self.assertFalse(missing_ok)
        self.assertIn("用户 ID", missing_error)

        session = {
            "access_token": "access-token",
            "access_user_id": "21",
            "browser_session_id": "session-21",
            "browser_refresh_cookie": "new_api_refresh=refresh-token",
        }
        with patch.object(
            app, "fetch_newapi_account_with_headers", return_value=(True, {"id": 21}, None)
        ) as account, patch.object(
            app,
            "fetch_newapi_groups_with_headers",
            return_value=(True, {"success": True, "data": {"default": 1}}, None),
        ) as groups:
            ok, payload, error = app.validate_newapi_site_browser_session(
                "https://newapi.example/path", session
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["account"]["id"], 21)
        account_headers = account.call_args.args[1]
        self.assertEqual(account.call_args.args[0], "https://newapi.example/path")
        self.assertEqual(account_headers["Authorization"], "Bearer access-token")
        self.assertEqual(account_headers["X-Auth-Session"], "session-21")
        self.assertEqual(
            account_headers["Cookie"], "new_api_refresh=refresh-token"
        )
        self.assertEqual(account_headers["New-Api-User"], "21")
        self.assertEqual(groups.call_args.args[1], account_headers)

    def test_validation_rejects_account_identity_mismatch(self):
        session = {"access_token": "access-token", "access_user_id": "21"}
        with patch.object(
            app, "fetch_newapi_account_with_headers", return_value=(True, {"id": 99}, None)
        ), patch.object(app, "fetch_newapi_groups_with_headers") as groups:
            ok, _payload, error = app.validate_newapi_site_browser_session(
                "https://newapi.example", session
            )

        self.assertFalse(ok)
        self.assertIn("不匹配", error)
        groups.assert_not_called()

    def test_refresh_uses_exact_site_origin_and_persists_rotated_bundle(self):
        site = {
            "id": 7,
            "base_url": "https://newapi.example/nested/path",
            "access_token": "old-access",
            "access_user_id": "21",
            "browser_refresh_cookie": "new_api_refresh=old-refresh",
            "browser_session_id": "session-21",
            "browser_access_expires_at": 1,
        }
        refreshed_payload = {
            "success": True,
            "data": {
                "access_token": "new-access",
                "access_expires_at": 4102444800,
                "user": {"id": 21},
                "session": {"sid": "session-21"},
            },
        }
        with patch.object(app, "db_query_one", return_value=None), patch.object(
            app,
            "request_json_with_headers",
            return_value=(
                True,
                refreshed_payload,
                None,
                {"set-cookie": ["new_api_refresh=new-refresh; Path=/; HttpOnly"]},
            ),
        ) as request, patch.object(
            app, "persist_newapi_site_browser_session"
        ) as persist:
            ok, error = app.refresh_newapi_site_browser_session(site, force=True)

        self.assertTrue(ok)
        self.assertIsNone(error)
        url = request.call_args.args[0]
        headers = request.call_args.kwargs["headers"]
        self.assertEqual(url, "https://newapi.example/nested/path/api/user/auth/refresh")
        self.assertEqual(headers["Origin"], "https://newapi.example")
        self.assertEqual(headers["X-Auth-Session"], "session-21")
        self.assertEqual(headers["Cookie"], "new_api_refresh=old-refresh")
        self.assertEqual(request.call_args.kwargs["method"], "POST")
        saved = persist.call_args.args[1]
        self.assertEqual(saved["access_token"], "new-access")
        self.assertEqual(saved["access_user_id"], "21")
        self.assertEqual(saved["browser_refresh_cookie"], "new_api_refresh=new-refresh")
        self.assertEqual(saved["browser_session_id"], "session-21")

    def test_ensure_accepts_legacy_session_and_refreshes_expired_modern_session(self):
        legacy = {
            "id": 7,
            "access_token": "legacy-token",
            "access_user_id": "21",
            "browser_session_id": "",
            "browser_refresh_cookie": "",
            "browser_access_expires_at": 0,
        }
        with patch.object(app, "refresh_newapi_site_browser_session") as refresh:
            legacy_ok, legacy_error = app.ensure_newapi_site_browser_session(legacy)
        self.assertTrue(legacy_ok)
        self.assertIsNone(legacy_error)
        refresh.assert_not_called()

        modern = {
            **legacy,
            "browser_session_id": "session-21",
            "browser_refresh_cookie": "new_api_refresh=refresh-token",
            "browser_access_expires_at": 1,
        }
        with patch.object(
            app, "refresh_newapi_site_browser_session", return_value=(True, None)
        ) as refresh:
            modern_ok, modern_error = app.ensure_newapi_site_browser_session(modern)
        self.assertTrue(modern_ok)
        self.assertIsNone(modern_error)
        refresh.assert_called_once_with(modern)

    def test_browser_runtime_account_and_groups_use_session_headers(self):
        site = {
            "id": 7,
            "base_url": "https://newapi.example",
            "auth_mode": "browser",
            "access_token": "dashboard-access",
            "access_user_id": "21",
            "browser_session_id": "session-21",
            "browser_refresh_cookie": "new_api_refresh=refresh-token",
            "browser_access_expires_at": 4102444800,
        }
        with patch.object(
            app, "ensure_newapi_site_browser_session", return_value=(True, None)
        ), patch.object(
            app, "db_query_one", return_value=None
        ), patch.object(
            app,
            "request_json",
            side_effect=[
                (True, {"success": True, "data": {"id": 21}}, None),
                (True, {"success": True, "data": []}, None),
            ],
        ) as request_json:
            account_result = app.fetch_newapi_account_for_site(site)
            groups_result = app.fetch_newapi_groups_for_site(site)

        self.assertTrue(account_result[0])
        self.assertTrue(groups_result[0])
        headers = request_json.call_args_list[0].kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer dashboard-access")
        self.assertEqual(headers["X-Auth-Session"], "session-21")
        self.assertEqual(headers["Cookie"], "new_api_refresh=refresh-token")
        self.assertEqual(headers["New-Api-User"], "21")

    def test_site_summary_exposes_only_redacted_browser_state(self):
        site = {
            "id": 7,
            "name": "NewAPI",
            "base_url": "https://newapi.example",
            "platform": "newapi",
            "enabled": 1,
            "interval_minutes": 3,
            "login_enabled": 1,
            "auth_mode": "browser",
            "access_token": "secret-access-token",
            "access_user_id": "21",
            "browser_refresh_cookie": "new_api_refresh=secret-refresh",
            "browser_session_id": "secret-session-id",
            "browser_access_expires_at": 4102444800,
            "status": "ok",
            "last_error": None,
            "last_check_at": None,
            "next_check_at": None,
            "consecutive_failures": 0,
        }
        with patch.object(app, "db_query_one", return_value=None):
            payload = app.site_summary(site)

        self.assertTrue(payload["has_access_token"])
        self.assertTrue(payload["has_browser_session"])
        rendered = repr(payload)
        self.assertNotIn("secret-access-token", rendered)
        self.assertNotIn("secret-refresh", rendered)
        self.assertNotIn("secret-session-id", rendered)
        self.assertFalse(any(re.search(r"refresh_cookie|session_id", key) for key in payload))

    def test_completion_accepts_legacy_newapi_site_session(self):
        body = {
            "status": "session_found",
            "platform": "newapi",
            "observed_origin": "https://newapi.example",
            "session": {
                "access_token": "legacy-access",
                "access_user_id": "21",
            },
        }
        site = {
            "id": 7,
            "platform": "newapi",
            "auth_mode": "browser",
            "base_url": "https://newapi.example",
        }
        fake_request = {
            "id": "newapi-request",
            "site_id": 7,
            "admin_site_id": None,
            "platform": "newapi",
            "target_origin": "https://newapi.example",
            "status": "validating",
        }
        with patch.object(
            app,
            "claim_session_sync_request",
            return_value=(True, self._completion_request(), None),
        ), patch.object(app, "db_query_one", side_effect=[site, site, fake_request]), patch.object(
            app,
            "validate_newapi_site_browser_session",
            return_value=(
                True,
                {"account": {"id": 21, "role": 10}, "groups": {}},
                None,
            ),
        ) as validate, patch.object(
            app, "persist_newapi_site_browser_session_cas", return_value=True
        ) as persist, patch.object(app, "finish_session_sync_request"), patch.object(
            app, "detect_site", return_value={"success": True}
        ):
            status, payload = app.complete_session_sync_request(
                "newapi-request", "secret", body
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ready")
        validate.assert_called_once_with(site["base_url"], body["session"])
        persist.assert_called_once_with(7, body["session"], "newapi-request", "https://newapi.example")
        self.assertNotIn("legacy-access", repr(payload))

    def test_completion_accepts_modern_newapi_site_session(self):
        body = self._modern_completion_body()
        site = {
            "id": 7,
            "platform": "newapi",
            "auth_mode": "browser",
            "base_url": "https://newapi.example/path",
        }
        fake_request = {
            "id": "newapi-request",
            "site_id": 7,
            "admin_site_id": None,
            "platform": "newapi",
            "target_origin": "https://newapi.example",
            "status": "validating",
        }
        with patch.object(
            app,
            "claim_session_sync_request",
            return_value=(True, self._completion_request(), None),
        ), patch.object(app, "db_query_one", side_effect=[site, site, fake_request]), patch.object(
            app,
            "validate_newapi_site_browser_session",
            return_value=(True, {"account": {"id": 21}, "groups": {}}, None),
        ) as validate, patch.object(
            app, "persist_newapi_site_browser_session_cas", return_value=True
        ) as persist, patch.object(app, "finish_session_sync_request"), patch.object(
            app, "detect_site", return_value={"success": True}
        ):
            status, payload = app.complete_session_sync_request(
                "newapi-request", "secret", body
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload["detected"])
        validate.assert_called_once_with(site["base_url"], body["session"])
        persist.assert_called_once_with(7, body["session"], "newapi-request", "https://newapi.example")

    def test_completion_rejects_non_exact_newapi_refresh_cookie_before_claim(self):
        for cookie in (
            "other_refresh=refresh-secret",
            "new_api_refresh=refresh-secret; injected=value",
            "new_api_refresh=",
        ):
            with self.subTest(cookie=cookie), patch.object(
                app, "claim_session_sync_request"
            ) as claim:
                body = self._modern_completion_body()
                body["session"]["browser_refresh_cookie"] = cookie
                status, payload = app.complete_session_sync_request(
                    "newapi-request", "secret", body
                )

            self.assertEqual(status, 400)
            self.assertEqual(payload["code"], "SESSION_COOKIE_INVALID")
            self.assertNotIn("refresh-secret", repr(payload))
            claim.assert_not_called()

    def test_completion_persists_admin_browser_fields_without_system_credentials(self):
        body = self._modern_completion_body()
        admin_site = {
            "id": 13,
            "platform": "newapi",
            "base_url": "https://newapi.example/dashboard",
            "access_token": "system-token",
            "security_proof": "two-factor-proof",
        }
        fake_request = {
            "id": "newapi-request",
            "site_id": None,
            "admin_site_id": 13,
            "platform": "newapi",
            "target_origin": "https://newapi.example",
            "status": "validating",
        }
        with patch.object(
            app,
            "claim_session_sync_request",
            return_value=(
                True,
                self._completion_request(target_kind="admin_site"),
                None,
            ),
        ), patch.object(app, "db_query_one", side_effect=[admin_site, admin_site, fake_request]), patch.object(
            app,
            "validate_newapi_site_browser_session",
            return_value=(
                True,
                {"account": {"id": 21, "role": 10}, "groups": {}},
                None,
            ),
        ), patch.object(
            app, "persist_admin_browser_auth_cas", return_value=True
        ) as persist_cas, patch.object(
            app, "db_execute_rowcount", return_value=1
        ) as execute, patch.object(
            app, "finish_session_sync_request"
        ) as finish, patch.object(app, "detect_site") as detect:
            status, payload = app.complete_session_sync_request(
                "newapi-request", "secret", body
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ready")
        # The CAS writer was called with the dedicated browser-only fields,
        # never the system access token or security proof.  The in-memory
        # admin row keeps its system credentials intact.
        kwargs = persist_cas.call_args.kwargs
        self.assertEqual(kwargs["access_token"], "dashboard-access")
        self.assertEqual(kwargs["session_id"], "session-21")
        self.assertEqual(kwargs["refresh_cookie"], "new_api_refresh=refresh-secret")
        self.assertEqual(admin_site["access_token"], "system-token")
        self.assertEqual(admin_site["security_proof"], "two-factor-proof")
        finish.assert_called_once_with("newapi-request", "ready")
        detect.assert_not_called()
        for call in execute.call_args_list:
            self.assertNotIn("security_proof", call.args[0])

    def test_completion_rejects_non_admin_session_for_admin_target(self):
        body = self._modern_completion_body()
        admin_site = {
            "id": 13,
            "platform": "newapi",
            "base_url": "https://newapi.example/dashboard",
            "access_token": "system-token",
            "security_proof": "two-factor-proof",
        }
        with patch.object(
            app,
            "claim_session_sync_request",
            return_value=(
                True,
                self._completion_request(target_kind="admin_site"),
                None,
            ),
        ), patch.object(app, "db_query_one", return_value=admin_site), patch.object(
            app,
            "validate_newapi_site_browser_session",
            return_value=(
                True,
                {"account": {"id": 21, "role": 1}, "groups": {}},
                None,
            ),
        ), patch.object(app, "db_execute") as execute, patch.object(
            app, "finish_session_sync_request"
        ) as finish:
            status, payload = app.complete_session_sync_request(
                "newapi-request", "secret", body
            )

        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "SESSION_INVALID")
        self.assertIn("管理员", payload["message"])
        execute.assert_not_called()
        finish.assert_called_once_with(
            "newapi-request",
            "expired",
            "SESSION_INVALID",
            "当前浏览器登录用户不是 NewAPI 管理员",
        )

    def test_invalid_newapi_session_does_not_overwrite_saved_auth(self):
        body = self._modern_completion_body()
        saved_site = {
            "id": 7,
            "platform": "newapi",
            "base_url": "https://newapi.example",
            "access_token": "saved-access",
            "browser_refresh_cookie": "new_api_refresh=saved-refresh",
        }
        with patch.object(
            app,
            "claim_session_sync_request",
            return_value=(True, self._completion_request(), None),
        ), patch.object(app, "db_query_one", return_value=saved_site), patch.object(
            app,
            "validate_newapi_site_browser_session",
            return_value=(False, {}, "登录态已过期，请重新登录"),
        ), patch.object(
            app, "persist_newapi_site_browser_session"
        ) as persist, patch.object(app, "finish_session_sync_request") as finish:
            status, payload = app.complete_session_sync_request(
                "newapi-request", "secret", body
            )

        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "SESSION_INVALID")
        persist.assert_not_called()
        finish.assert_called_once_with(
            "newapi-request",
            "expired",
            "SESSION_INVALID",
            "登录态已过期，请重新登录",
        )

    def test_newapi_origin_mismatch_is_redacted_and_consumes_request(self):
        body = self._modern_completion_body()
        body["observed_origin"] = "https://other.example"
        with patch.object(
            app,
            "claim_session_sync_request",
            return_value=(True, self._completion_request(), None),
        ), patch.object(app, "finish_session_sync_request") as finish, patch.object(
            app, "persist_newapi_site_browser_session"
        ) as persist:
            status, payload = app.complete_session_sync_request(
                "newapi-request", "secret", body
            )

        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "ORIGIN_MISMATCH")
        self.assertNotIn("refresh-secret", repr(payload))
        finish.assert_called_once_with(
            "newapi-request",
            "failed",
            "ORIGIN_MISMATCH",
            "同步站点 Origin 不匹配",
        )
        persist.assert_not_called()

    def test_newapi_completion_replay_never_reaches_persistence(self):
        with patch.object(
            app,
            "claim_session_sync_request",
            return_value=(False, None, "SYNC_REQUEST_CONSUMED"),
        ), patch.object(app, "persist_newapi_site_browser_session") as persist:
            status, payload = app.complete_session_sync_request(
                "newapi-request", "secret", self._modern_completion_body()
            )

        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "SYNC_REQUEST_CONSUMED")
        persist.assert_not_called()

    def test_create_newapi_browser_site_does_not_require_manual_tokens(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/sites"
        body = {
            "name": "NewAPI browser",
            "base_url": "https://newapi.example",
            "platform": "newapi",
            "auth_mode": "browser",
            "login_enabled": True,
            "enabled": True,
            "interval_minutes": 3,
        }
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value=body
        ), patch.object(app, "db_execute", return_value=77) as execute, patch.object(
            app, "json_response"
        ) as response:
            app.Handler.do_POST(handler)

        self.assertTrue(execute.called, response.call_args.args[1])
        sql, params = execute.call_args.args
        self.assertIn("INSERT INTO sites", sql)
        self.assertEqual(params[5], 1)
        self.assertEqual(params[6], "browser")
        self.assertEqual(params[9], "")
        self.assertEqual(params[10], "")
        self.assertEqual(response.call_args.args[1], {"success": True, "id": 77})

    def test_create_newapi_manual_token_site_keeps_token_mode(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/sites"
        body = {
            "name": "NewAPI token",
            "base_url": "https://newapi.example",
            "platform": "newapi",
            "auth_mode": "token",
            "login_enabled": True,
            "access_token": "system-access",
            "access_user_id": "21",
            "enabled": True,
            "interval_minutes": 3,
        }
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value=body
        ), patch.object(app, "db_execute", return_value=78) as execute, patch.object(
            app, "json_response"
        ) as response:
            app.Handler.do_POST(handler)

        _sql, params = execute.call_args.args
        self.assertEqual(params[6], "token")
        self.assertEqual(params[9], "system-access")
        self.assertEqual(params[10], "21")
        self.assertEqual(response.call_args.args[1], {"success": True, "id": 78})

    def test_update_newapi_browser_site_preserves_synced_session_on_empty_form(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/sites/7"
        existing = {
            "id": 7,
            "platform": "newapi",
            "auth_mode": "browser",
            "login_enabled": 1,
            "access_token": "synced-access",
            "access_user_id": "21",
            "browser_refresh_cookie": "new_api_refresh=synced-refresh",
            "browser_session_id": "synced-session",
            "browser_access_expires_at": 4102444800,
        }
        body = {
            "platform": "newapi",
            "login_enabled": True,
            "auth_mode": "browser",
        }
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value=body
        ), patch.object(app, "db_query_one", return_value=existing), patch.object(
            app, "db_execute", return_value=1
        ) as execute, patch.object(app, "invalidate_site_model_cache"), patch.object(
            app, "schedule_model_cache_refresh"
        ), patch.object(app, "json_response") as response:
            app.Handler.do_PUT(handler)

        sql, params = execute.call_args.args
        self.assertIn("auth_mode = ?", sql)
        self.assertIn("browser", params)
        self.assertNotRegex(sql, r"(?<!browser_)access_token\s*=\s*\?")
        self.assertNotIn("access_user_id = ?", sql)
        self.assertNotIn("browser_refresh_cookie = ?", sql)
        self.assertNotIn("browser_session_id = ?", sql)
        self.assertEqual(response.call_args.args[1], {"success": True})

    def test_switching_newapi_token_site_to_browser_clears_manual_auth(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/sites/7"
        existing = {
            "id": 7,
            "platform": "newapi",
            "auth_mode": "token",
            "login_enabled": 1,
            "access_token": "system-access",
            "access_user_id": "21",
            "browser_refresh_cookie": None,
            "browser_session_id": None,
            "browser_access_expires_at": 0,
        }
        body = {
            "platform": "newapi",
            "login_enabled": True,
            "auth_mode": "browser",
        }
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value=body
        ), patch.object(app, "db_query_one", return_value=existing), patch.object(
            app, "db_execute", return_value=1
        ) as execute, patch.object(app, "invalidate_site_model_cache"), patch.object(
            app, "schedule_model_cache_refresh"
        ), patch.object(app, "json_response") as response:
            app.Handler.do_PUT(handler)

        sql, params = execute.call_args.args
        self.assertRegex(sql, r"(?<!browser_)access_token\s*=\s*\?")
        self.assertIn("access_user_id = ?", sql)
        self.assertIn("browser_refresh_cookie = ?", sql)
        self.assertIn("browser_session_id = ?", sql)
        self.assertIn("session_sync_status = ?", sql)
        self.assertIn("not_requested", params)
        self.assertNotIn("system-access", params)
        self.assertEqual(response.call_args.args[1], {"success": True})

    def test_switching_newapi_browser_site_to_token_requires_manual_auth(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/sites/7"
        existing = {
            "id": 7,
            "platform": "newapi",
            "auth_mode": "browser",
            "login_enabled": 1,
            "access_token": "browser-access",
            "access_user_id": "21",
            "browser_refresh_cookie": "new_api_refresh=refresh-secret",
            "browser_session_id": "session-21",
        }
        body = {
            "platform": "newapi",
            "login_enabled": True,
            "auth_mode": "token",
        }
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value=body
        ), patch.object(app, "db_query_one", return_value=existing), patch.object(
            app, "db_execute"
        ) as execute, patch.object(app, "json_response") as response:
            app.Handler.do_PUT(handler)

        self.assertFalse(response.call_args.args[1].get("success", True))
        self.assertEqual(response.call_args.args[2], 400)
        self.assertIn("系统访问令牌", response.call_args.args[1]["message"])
        execute.assert_not_called()

    def test_switching_newapi_browser_site_to_token_clears_browser_state(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/sites/7"
        existing = {
            "id": 7,
            "platform": "newapi",
            "auth_mode": "browser",
            "login_enabled": 1,
            "access_token": "browser-access",
            "access_user_id": "21",
            "browser_refresh_cookie": "new_api_refresh=refresh-secret",
            "browser_session_id": "session-21",
            "session_sync_status": "ready",
        }
        body = {
            "platform": "newapi",
            "login_enabled": True,
            "auth_mode": "token",
            "access_token": "system-access",
            "access_user_id": "42",
        }
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value=body
        ), patch.object(app, "db_query_one", return_value=existing), patch.object(
            app, "db_execute", return_value=1
        ) as execute, patch.object(app, "invalidate_site_model_cache"), patch.object(
            app, "schedule_model_cache_refresh"
        ), patch.object(app, "json_response") as response:
            app.Handler.do_PUT(handler)

        sql, params = execute.call_args.args
        self.assertIn("browser_refresh_cookie = ?", sql)
        self.assertIn("browser_session_id = ?", sql)
        self.assertIn("browser_access_expires_at = ?", sql)
        self.assertIn("session_sync_status = ?", sql)
        self.assertIn("system-access", params)
        self.assertIn("42", params)
        self.assertIn("not_requested", params)
        self.assertEqual(response.call_args.args[1], {"success": True})

    def test_switching_newapi_browser_site_to_sub2api_clears_newapi_session(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/sites/7"
        existing = {
            "id": 7,
            "platform": "newapi",
            "auth_mode": "browser",
            "login_enabled": 1,
            "access_token": "browser-access",
            "access_user_id": "21",
            "browser_refresh_cookie": "new_api_refresh=refresh-secret",
            "browser_session_id": "session-21",
            "browser_access_expires_at": 4102444800,
            "session_sync_status": "ready",
        }
        body = {
            "platform": "sub2api",
            "login_enabled": True,
            "auth_mode": "password",
            "login_username": "user@example.com",
            "login_password": "password",
        }
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value=body
        ), patch.object(app, "db_query_one", return_value=existing), patch.object(
            app, "db_execute", return_value=1
        ) as execute, patch.object(app, "invalidate_site_model_cache"), patch.object(
            app, "schedule_model_cache_refresh"
        ), patch.object(app, "json_response") as response:
            app.Handler.do_PUT(handler)

        sql, params = execute.call_args.args
        self.assertIn("browser_refresh_cookie = ?", sql)
        self.assertIn("browser_session_id = ?", sql)
        self.assertIn("browser_access_expires_at = ?", sql)
        self.assertIn("session_sync_status = ?", sql)
        self.assertIn("not_requested", params)
        self.assertEqual(response.call_args.args[1], {"success": True})


if __name__ == "__main__":
    unittest.main()
