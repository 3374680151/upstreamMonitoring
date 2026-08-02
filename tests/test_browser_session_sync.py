import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

import app


class BrowserSessionSyncTests(unittest.TestCase):
    def test_cookie_permission_failure_has_actionable_terminal_state(self):
        self.assertEqual(
            app.SESSION_SYNC_PAGE_FAILURES.get("COOKIE_PERMISSION_REQUIRED"),
            (
                "permission_required",
                "扩展需要读取 NewAPI 登录 Cookie 的权限，请允许后重新同步",
            ),
        )

    def test_site_schema_exposes_browser_sync_columns(self):
        self.assertEqual(
            app.SITES_COLUMN_ADDITIONS["session_sync_status"],
            "VARCHAR(32) NOT NULL DEFAULT 'not_requested'",
        )
        self.assertEqual(app.SITES_COLUMN_ADDITIONS["session_sync_error"], "TEXT")
        self.assertEqual(app.SITES_COLUMN_ADDITIONS["session_synced_at"], "VARCHAR(40)")
        ddl = "\n".join(app.DDL_STATEMENTS)
        self.assertIn("browser_session_sync_requests", ddl)
        self.assertIn("FOREIGN KEY (site_id)", ddl)
        self.assertIn("FOREIGN KEY (admin_site_id)", ddl)
        self.assertIn("(site_id IS NOT NULL) <> (admin_site_id IS NOT NULL)", ddl)

    def test_normalize_session_expiry_accepts_ms_seconds_and_iso(self):
        expected = "2026-07-29T23:46:40+08:00"
        self.assertEqual(app.normalize_session_expiry("1785340000000"), expected)
        self.assertEqual(app.normalize_session_expiry("1785340000"), expected)
        self.assertEqual(
            app.normalize_session_expiry("2026-07-29T23:00:00+08:00"),
            "2026-07-29T23:00:00+08:00",
        )
        self.assertEqual(app.normalize_session_expiry("bad"), "")

    def test_site_origin_requires_exact_http_origin_without_credentials(self):
        self.assertEqual(
            app.site_origin("https://example.com/path?q=1"),
            "https://example.com",
        )
        self.assertEqual(
            app.site_origin("http://localhost:8080/path"),
            "http://localhost:8080",
        )
        self.assertEqual(app.site_origin("https://user:pass@example.com"), "")
        self.assertEqual(app.site_origin("javascript:alert(1)"), "")
        self.assertEqual(app.site_origin("https://[broken"), "")

    def test_create_request_binds_site_platform_and_origin(self):
        site = {
            "id": 7,
            "base_url": "https://example.com/api/path",
            "platform": "sub2api",
            "auth_mode": "browser",
        }
        with patch.object(app, "db_query_one", return_value=site), patch.object(
            app, "db_execute", return_value=1
        ) as execute, patch.object(
            app.secrets,
            "token_urlsafe",
            side_effect=["request-id", "plain-secret"],
        ):
            ok, payload, error = app.create_site_session_sync_request(7)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["request_id"], "request-id")
        self.assertEqual(payload["secret"], "plain-secret")
        self.assertEqual(payload["platform"], "sub2api")
        self.assertEqual(payload["target_origin"], "https://example.com")
        insert_call = next(
            call for call in execute.call_args_list
            if "INSERT INTO browser_session_sync_requests" in call.args[0]
        )
        self.assertEqual(insert_call.args[1][1], 7)
        self.assertEqual(insert_call.args[1][3], "sub2api")
        self.assertEqual(insert_call.args[1][4], "https://example.com")

    def test_new_request_expires_previous_pending_request(self):
        site = {
            "id": 9,
            "base_url": "https://example.com",
            "platform": "sub2api",
            "auth_mode": "browser",
        }
        with patch.object(app, "db_query_one", return_value=site), patch.object(
            app, "db_execute", return_value=1
        ) as execute:
            ok, _payload, _error = app.create_site_session_sync_request(9)

        self.assertTrue(ok)
        invalidation = execute.call_args_list[0]
        self.assertIn("UPDATE browser_session_sync_requests", invalidation.args[0])
        self.assertIn("status IN ('pending', 'validating')", invalidation.args[0])
        self.assertEqual(invalidation.args[1][-1], 9)

    def test_secret_is_hashed_and_never_returned_by_status_payload(self):
        site = {
            "id": 11,
            "base_url": "https://example.com",
            "platform": "sub2api",
            "auth_mode": "browser",
        }
        with patch.object(app, "db_query_one", return_value=site), patch.object(
            app, "db_execute", return_value=1
        ) as execute, patch.object(
            app.secrets,
            "token_urlsafe",
            side_effect=["request-id", "plain-secret"],
        ):
            ok, created, _error = app.create_site_session_sync_request(11)

        self.assertTrue(ok)
        insert_call = next(
            call for call in execute.call_args_list
            if "INSERT INTO browser_session_sync_requests" in call.args[0]
        )
        self.assertNotIn("plain-secret", insert_call.args[1])
        self.assertEqual(
            insert_call.args[1][5],
            app.hash_session_sync_secret("plain-secret"),
        )
        request_row = {
            "id": created["request_id"],
            "site_id": 11,
            "platform": "sub2api",
            "target_origin": "https://example.com",
            "secret_hash": insert_call.args[1][5],
            "status": "pending",
            "error_code": None,
            "error_message": None,
            "expires_at": (app.app_now() + timedelta(seconds=30)).isoformat(),
            "created_at": app.utc_now_iso(),
            "updated_at": app.utc_now_iso(),
            "consumed_at": None,
        }
        with patch.object(app, "db_query_one", return_value=request_row):
            status = app.get_site_session_sync_request(11, created["request_id"])
        self.assertNotIn("secret", status)
        self.assertNotIn("secret_hash", status)

    def test_expired_or_consumed_request_cannot_be_claimed(self):
        expired = {
            "id": "expired",
            "site_id": 1,
            "platform": "sub2api",
            "target_origin": "https://example.com",
            "secret_hash": app.hash_session_sync_secret("secret"),
            "status": "pending",
            "expires_at": (app.app_now() - timedelta(seconds=1)).isoformat(),
        }
        consumed = {
            **expired,
            "id": "consumed",
            "status": "ready",
            "expires_at": (app.app_now() + timedelta(seconds=30)).isoformat(),
        }
        with patch.object(app, "db_query_one", side_effect=[expired, consumed]), patch.object(
            app, "db_execute", return_value=1
        ):
            expired_result = app.claim_session_sync_request("expired", "secret")
            consumed_result = app.claim_session_sync_request("consumed", "secret")
        self.assertFalse(expired_result[0])
        self.assertEqual(expired_result[2], "SYNC_REQUEST_EXPIRED")
        self.assertFalse(consumed_result[0])
        self.assertEqual(consumed_result[2], "SYNC_REQUEST_CONSUMED")

    def test_claim_uses_constant_time_secret_comparison(self):
        row = {
            "id": "request-id",
            "site_id": 1,
            "platform": "sub2api",
            "target_origin": "https://example.com",
            "secret_hash": app.hash_session_sync_secret("correct"),
            "status": "pending",
            "expires_at": (app.app_now() + timedelta(seconds=30)).isoformat(),
        }
        with patch.object(app, "db_query_one", return_value=row), patch.object(
            app, "db_execute", return_value=1
        ), patch.object(
            app.hmac,
            "compare_digest",
            wraps=app.hmac.compare_digest,
        ) as compare:
            ok, claimed, error = app.claim_session_sync_request(
                "request-id", "correct"
            )
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(claimed["status"], "validating")
        compare.assert_called_once()

    def test_valid_browser_session_requires_account_and_groups(self):
        with patch.object(
            app,
            "fetch_sub2api_account_by_token",
            return_value=(True, {"id": 3, "email": "user@example.com"}, None),
        ) as account, patch.object(
            app,
            "fetch_sub2api_groups_by_token",
            return_value=(True, {"success": True, "data": []}, None),
        ) as groups:
            ok, data, error = app.validate_sub2api_browser_session(
                "https://example.com", "access-token"
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(data["account"]["id"], 3)
        self.assertEqual(data["groups"]["data"], [])
        account.assert_called_once_with("https://example.com", "access-token")
        groups.assert_called_once_with("https://example.com", "access-token")

    def test_invalid_browser_session_does_not_overwrite_saved_tokens(self):
        with patch.object(
            app,
            "validate_sub2api_browser_session",
            return_value=(False, {}, "登录态已过期，请重新登录"),
        ), patch.object(app, "db_execute", return_value=1) as execute:
            ok, error = app.apply_sub2api_browser_session(
                3,
                "https://example.com",
                {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "token_expires_at": "1785340000000",
                },
            )

        self.assertFalse(ok)
        self.assertEqual(error, "登录态已过期，请重新登录")
        sql = "\n".join(call.args[0] for call in execute.call_args_list)
        self.assertNotIn("access_token = ?", sql)
        self.assertNotIn("refresh_token = ?", sql)
        self.assertIn("session_sync_status = 'expired'", sql)

    def test_replaced_sync_request_cannot_apply_saved_session(self):
        site = {
            "id": 3,
            "platform": "sub2api",
            "auth_mode": "browser",
            "base_url": "https://example.com",
            "access_token": "current-access",
            "refresh_token": "current-refresh",
        }
        replaced_request = {"id": "stale-request", "status": "expired"}
        with patch.object(
            app, "db_query_one", side_effect=[site, replaced_request]
        ), patch.object(
            app, "validate_sub2api_browser_session"
        ) as validate, patch.object(app, "persist_site_browser_session") as persist:
            ok, error = app.apply_sub2api_browser_session(
                3,
                "https://example.com",
                {
                    "access_token": "stale-access",
                    "refresh_token": "stale-refresh",
                    "token_expires_at": "1785340000000",
                },
                request_id="stale-request",
                expected_origin="https://example.com",
            )

        self.assertFalse(ok)
        self.assertIn("已失效", error)
        validate.assert_not_called()
        persist.assert_not_called()

    def test_finish_replaced_request_does_not_overwrite_site_sync_status(self):
        row = {"site_id": 3, "admin_site_id": None}
        with patch.object(app, "db_query_one", return_value=row), patch.object(
            app, "db_execute_rowcount", return_value=0
        ) as update_request, patch.object(app, "db_execute") as update_site:
            finished = app.finish_session_sync_request(
                "stale-request", "ready"
            )

        self.assertFalse(finished)
        self.assertIn("status IN ('pending', 'validating')", update_request.call_args.args[0])
        update_site.assert_not_called()

    def test_browser_auth_mode_uses_token_path_and_refreshes(self):
        with patch.object(
            app,
            "fetch_sub2api_groups_by_token",
            side_effect=[
                (False, {"status": 401}, "HTTP 401"),
                (True, {"success": True, "data": []}, None),
            ],
        ) as groups, patch.object(
            app,
            "refresh_sub2api_auth",
            return_value=(
                True,
                {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                },
                None,
            ),
        ) as refresh, patch.object(app, "sub2api_login") as login:
            ok, payload, error = app.fetch_sub2api_user_groups(
                "https://example.com",
                auth_mode="browser",
                access_token="old-access",
                refresh_token="old-refresh",
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["refreshed_auth"]["access_token"], "new-access")
        self.assertEqual(groups.call_count, 2)
        refresh.assert_called_once()
        login.assert_not_called()

    def test_browser_first_auth_uses_access_token_without_password(self):
        with patch.object(
            app,
            "fetch_sub2api_groups_by_token",
            return_value=(True, {"success": True, "data": []}, None),
        ) as groups, patch.object(app, "refresh_sub2api_auth") as refresh, patch.object(
            app, "sub2api_login"
        ) as login, patch.object(app, "persist_sub2api_refreshed_auth") as persist:
            ok, payload, error = app.fetch_sub2api_user_groups(
                "https://example.com",
                username="user@example.com",
                password="saved-password",
                auth_mode="browser",
                access_token="valid-access",
                refresh_token="valid-refresh",
                site_id=5,
            )

        self.assertTrue(ok)
        self.assertEqual(payload["data"], [])
        self.assertIsNone(error)
        groups.assert_called_once_with("https://example.com", "valid-access")
        refresh.assert_not_called()
        login.assert_not_called()
        persist.assert_not_called()

    def test_browser_first_auth_refreshes_and_persists_rotated_session(self):
        refreshed = {
            "access_token": "rotated-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
        }
        with patch.object(
            app,
            "fetch_sub2api_groups_by_token",
            side_effect=[
                (False, {"status": 401}, "HTTP 401"),
                (True, {"success": True, "data": []}, None),
            ],
        ), patch.object(
            app, "refresh_sub2api_auth", return_value=(True, refreshed, None)
        ), patch.object(app, "sub2api_login") as login, patch.object(
            app, "persist_sub2api_refreshed_auth"
        ) as persist:
            ok, payload, error = app.fetch_sub2api_user_groups(
                "https://example.com",
                username="user@example.com",
                password="saved-password",
                auth_mode="browser",
                access_token="expired-access",
                refresh_token="old-refresh",
                site_id=5,
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["refreshed_auth"], refreshed)
        persist.assert_called_once()
        self.assertEqual(persist.call_args.args[:2], (5, refreshed))
        self.assertEqual(
            persist.call_args.kwargs,
            {
                "expected_access_token": "expired-access",
                "expected_refresh_token": "old-refresh",
                "restore_browser_session": True,
            },
        )
        login.assert_not_called()

    def test_browser_first_auth_falls_back_to_password_after_refresh_failure(self):
        login_payload = {
            "code": 0,
            "data": {
                "access_token": "login-access",
                "refresh_token": "login-refresh",
                "expires_in": 7200,
            },
        }
        with patch.object(
            app,
            "fetch_sub2api_groups_by_token",
            side_effect=[
                (False, {"status": 401}, "HTTP 401"),
                (True, {"success": True, "data": []}, None),
            ],
        ) as groups, patch.object(
            app,
            "refresh_sub2api_auth",
            return_value=(False, {"status": 401}, "HTTP 401"),
        ) as refresh, patch.object(
            app,
            "sub2api_login",
            return_value=(True, "login-access", login_payload, None),
        ) as login, patch.object(
            app, "persist_sub2api_refreshed_auth"
        ) as persist:
            ok, payload, error = app.fetch_sub2api_user_groups(
                "https://example.com",
                username="user@example.com",
                password="saved-password",
                auth_mode="browser",
                access_token="expired-access",
                refresh_token="expired-refresh",
                site_id=5,
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(groups.call_count, 2)
        refresh.assert_called_once()
        login.assert_called_once_with(
            "https://example.com", "user@example.com", "saved-password"
        )
        persisted = persist.call_args.args[1]
        self.assertEqual(persisted["access_token"], "login-access")
        self.assertEqual(persisted["refresh_token"], "login-refresh")
        self.assertEqual(payload["refreshed_auth"], persisted)

    def test_browser_first_auth_reports_browser_sync_for_interactive_login(self):
        with patch.object(
            app,
            "fetch_sub2api_groups_by_token",
            return_value=(False, {"status": 401}, "HTTP 401"),
        ), patch.object(
            app,
            "refresh_sub2api_auth",
            return_value=(False, {"status": 401}, "HTTP 401"),
        ), patch.object(
            app,
            "sub2api_login",
            return_value=(
                False,
                "",
                {
                    "code": "TURNSTILE_VERIFICATION_FAILED",
                    "message": "Turnstile verification failed",
                },
                "HTTP 400",
            ),
        ), patch.object(app, "persist_sub2api_refreshed_auth") as persist:
            ok, payload, error = app.fetch_sub2api_user_groups(
                "https://example.com",
                username="user@example.com",
                password="saved-password",
                auth_mode="browser",
                access_token="expired-access",
                refresh_token="expired-refresh",
                site_id=5,
            )

        self.assertFalse(ok)
        self.assertEqual(error, "请先在浏览器登录并同步")
        self.assertEqual(payload["code"], "BROWSER_SESSION_REQUIRED")
        self.assertTrue(payload["browser_sync_required"])
        persist.assert_not_called()

    def test_browser_first_auth_does_not_mask_transport_failure_with_password(self):
        with patch.object(
            app,
            "fetch_sub2api_groups_by_token",
            return_value=(False, {"status": 503}, "HTTP 503"),
        ), patch.object(app, "refresh_sub2api_auth") as refresh, patch.object(
            app, "sub2api_login"
        ) as login:
            ok, payload, error = app.fetch_sub2api_user_groups(
                "https://example.com",
                username="user@example.com",
                password="saved-password",
                auth_mode="browser",
                access_token="saved-access",
                refresh_token="saved-refresh",
                site_id=5,
            )

        self.assertFalse(ok)
        self.assertEqual(payload["status"], 503)
        self.assertEqual(error, "HTTP 503")
        refresh.assert_not_called()
        login.assert_not_called()

    def test_browser_first_account_401_enters_refresh_fallback(self):
        account_payload = {
            "id": 8,
            "email": "user@example.com",
            "balance": 12.5,
            "subscriptions": [],
        }
        refreshed = {
            "access_token": "rotated-account-access",
            "refresh_token": "rotated-account-refresh",
            "expires_in": 3600,
        }
        with patch.object(
            app,
            "fetch_sub2api_account_by_token",
            side_effect=[
                (False, {"account": {"status": 401}}, "HTTP 401"),
                (True, account_payload, None),
            ],
        ) as account, patch.object(
            app,
            "refresh_sub2api_auth",
            return_value=(True, refreshed, None),
        ) as refresh, patch.object(app, "sub2api_login") as login, patch.object(
            app, "persist_sub2api_refreshed_auth"
        ) as persist:
            ok, payload, error = app.fetch_sub2api_account(
                "https://example.com",
                username="user@example.com",
                password="saved-password",
                auth_mode="browser",
                access_token="expired-account-access",
                refresh_token="account-refresh",
                site_id=17,
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["id"], 8)
        self.assertEqual(account.call_count, 2)
        refresh.assert_called_once_with(
            "https://example.com", "expired-account-access", "account-refresh"
        )
        login.assert_not_called()
        persist.assert_called_once()
        self.assertEqual(persist.call_args.args[:2], (17, refreshed))
        self.assertEqual(
            persist.call_args.kwargs,
            {
                "expected_access_token": "expired-account-access",
                "expected_refresh_token": "account-refresh",
                "restore_browser_session": True,
            },
        )

    def test_browser_first_auth_401_then_refresh_503_does_not_try_password(self):
        with patch.object(
            app,
            "fetch_sub2api_groups_by_token",
            return_value=(False, {"status": 401}, "HTTP 401"),
        ), patch.object(
            app,
            "refresh_sub2api_auth",
            return_value=(False, {"status": 503}, "HTTP 503"),
        ) as refresh, patch.object(app, "sub2api_login") as login:
            ok, payload, error = app.fetch_sub2api_user_groups(
                "https://example.com",
                username="user@example.com",
                password="saved-password",
                auth_mode="browser",
                access_token="expired-access",
                refresh_token="refresh-token",
                site_id=18,
            )

        self.assertFalse(ok)
        self.assertEqual(error, "HTTP 503")
        self.assertEqual(payload["refresh"]["status"], 503)
        refresh.assert_called_once()
        login.assert_not_called()

    def test_browser_first_auth_401_then_refresh_401_password_503_returns_transport_error(self):
        with patch.object(
            app,
            "fetch_sub2api_groups_by_token",
            return_value=(False, {"status": 401}, "HTTP 401"),
        ), patch.object(
            app,
            "refresh_sub2api_auth",
            return_value=(False, {"refresh": {"status": 401}}, "HTTP 401"),
        ), patch.object(
            app,
            "sub2api_login",
            return_value=(False, "", {"status": 503}, "HTTP 503"),
        ) as login:
            ok, payload, error = app.fetch_sub2api_user_groups(
                "https://example.com",
                username="user@example.com",
                password="saved-password",
                auth_mode="browser",
                access_token="expired-access",
                refresh_token="expired-refresh",
                site_id=19,
            )

        self.assertFalse(ok)
        self.assertEqual(error, "HTTP 503")
        self.assertEqual(payload["login"]["status"], 503)
        login.assert_called_once_with(
            "https://example.com", "user@example.com", "saved-password"
        )

    def test_sub2api_failure_classifier_recognizes_nested_http_5xx(self):
        self.assertEqual(
            app.classify_sub2api_auth_failure(
                {"refresh": {"status": 503}}, "HTTP 503"
            ),
            "transport",
        )
        self.assertEqual(
            app.classify_sub2api_auth_failure(
                {"account": {"status": 401}}, "HTTP 401"
            ),
            "auth",
        )

    def test_rates_request_failure_is_not_reported_as_empty_rates(self):
        groups_payload = {
            "code": 0,
            "data": [{"id": 1, "name": "default", "rate_multiplier": 1}],
        }
        rates_payload = {"status": 503, "message": "upstream unavailable"}
        with patch.object(
            app,
            "request_json",
            side_effect=[
                (True, groups_payload, None),
                (False, rates_payload, "HTTP 503"),
            ],
        ):
            ok, payload, error = app.fetch_sub2api_groups_by_token(
                "https://example.com", "saved-access"
            )

        self.assertFalse(ok)
        self.assertEqual(error, "HTTP 503")
        self.assertEqual(payload["rates"], rates_payload)

    def test_rates_401_enters_browser_refresh_fallback(self):
        fresh_payload = {
            "success": True,
            "data": [{"id": 1, "name": "default", "rate_multiplier": 1}],
            "user_rates": {"1": 0.2},
        }
        rotated = {
            "access_token": "rotated-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
        }
        with patch.object(
            app,
            "fetch_sub2api_groups_by_token",
            side_effect=[
                (
                    False,
                    {"rates": {"status": 401, "message": "token expired"}},
                    "用户分组倍率响应失败",
                ),
                (True, fresh_payload, None),
            ],
        ), patch.object(
            app, "refresh_sub2api_auth", return_value=(True, rotated, None)
        ) as refresh, patch.object(app, "persist_sub2api_refreshed_auth"):
            ok, payload, error = app.fetch_sub2api_user_groups(
                "https://example.com",
                auth_mode="browser",
                access_token="expired-access",
                refresh_token="expired-refresh",
                site_id=24,
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["user_rates"], {"1": 0.2})
        refresh.assert_called_once_with(
            "https://example.com", "expired-access", "expired-refresh"
        )

    def test_rejected_rotated_token_does_not_restore_browser_sync_state(self):
        rotated = {
            "access_token": "rotated-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
        }
        with patch.object(
            app,
            "fetch_sub2api_groups_by_token",
            side_effect=[
                (False, {"status": 401}, "HTTP 401"),
                (False, {"status": 401}, "HTTP 401"),
            ],
        ), patch.object(
            app, "refresh_sub2api_auth", return_value=(True, rotated, None)
        ), patch.object(app, "persist_sub2api_refreshed_auth") as persist, patch.object(
            app, "sub2api_login", return_value=(False, "", {}, "HTTP 401")
        ):
            ok, _payload, _error = app.fetch_sub2api_user_groups(
                "https://example.com",
                username="user@example.com",
                password="saved-password",
                auth_mode="browser",
                access_token="expired-access",
                refresh_token="expired-refresh",
                site_id=23,
            )

        self.assertFalse(ok)
        persist.assert_called_once()
        self.assertFalse(persist.call_args.kwargs["restore_browser_session"])

    def test_browser_sync_persistence_preserves_saved_password_fallback(self):
        with patch.object(app, "db_execute", return_value=1) as execute:
            app.persist_site_browser_session(
                5, "synced-access", "synced-refresh", "1785340000000"
            )

        sql, params = execute.call_args.args
        self.assertNotIn("login_username", sql)
        self.assertNotIn("login_password", sql)
        self.assertNotIn("saved-password", repr(params))

    def test_successful_browser_refresh_restores_session_sync_state(self):
        rotated = {
            "access_token": "restored-access",
            "refresh_token": "restored-refresh",
            "expires_in": 3600,
        }
        with patch.object(app, "db_execute_rowcount", return_value=1) as execute:
            app.persist_sub2api_refreshed_auth(
                5,
                rotated,
                expected_access_token="expired-access",
                expected_refresh_token="expired-refresh",
                restore_browser_session=True,
            )

        sql, params = execute.call_args.args
        self.assertIn("session_sync_status = 'ready'", sql)
        self.assertIn("session_sync_error = NULL", sql)
        self.assertIn("COALESCE(access_token, '') = ?", sql)
        self.assertIn("COALESCE(refresh_token, '') = ?", sql)
        self.assertIn("expired-access", params)
        self.assertIn("expired-refresh", params)
        self.assertNotIn("login_username", sql)
        self.assertNotIn("login_password", sql)

    def test_refresh_writeback_cannot_overwrite_newer_session_after_cas_miss(self):
        rotated = {
            "access_token": "stale-rotated-access",
            "refresh_token": "stale-rotated-refresh",
            "expires_in": 3600,
        }
        with patch.object(app, "db_execute_rowcount", return_value=0) as execute:
            app.persist_sub2api_refreshed_auth(
                5,
                rotated,
                expected_access_token="old-access",
                expected_refresh_token="old-refresh",
                restore_browser_session=True,
            )

        self.assertEqual(execute.call_count, 1)
        sql, params = execute.call_args.args
        self.assertIn("WHERE id = ?", sql)
        self.assertIn("COALESCE(access_token, '') = ?", sql)
        self.assertIn("COALESCE(refresh_token, '') = ?", sql)
        self.assertEqual(params[-3:], (5, "old-access", "old-refresh"))

    def test_browser_auth_reloads_newer_database_session_before_refreshing_stale_copy(self):
        attempts = []

        def fetch(_base_url, token):
            attempts.append(token)
            if token == "newer-access":
                return True, {"success": True, "data": []}, None
            return False, {"status": 401}, "HTTP 401"

        current = {
            "id": 5,
            "platform": "sub2api",
            "base_url": "https://example.com",
            "auth_mode": "browser",
            "access_token": "newer-access",
            "refresh_token": "newer-refresh",
            "login_username": "",
            "login_password": "",
        }
        with patch.object(app, "fetch_sub2api_groups_by_token", side_effect=fetch), patch.object(
            app, "db_query_one", return_value=current
        ) as query, patch.object(app, "refresh_sub2api_auth") as refresh, patch.object(
            app, "sub2api_login"
        ) as login:
            ok, _payload, error = app.fetch_sub2api_user_groups(
                "https://example.com",
                auth_mode="browser",
                access_token="stale-access",
                refresh_token="stale-refresh",
                site_id=5,
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(attempts, ["newer-access"])
        self.assertTrue(query.called)
        refresh.assert_not_called()
        login.assert_not_called()

    def test_detect_success_update_does_not_write_refreshed_tokens_again(self):
        site = {
            "id": 7,
            "name": "sub2api",
            "platform": "sub2api",
            "base_url": "https://example.com",
            "auth_mode": "browser",
            "interval_minutes": 3,
            "consecutive_failures": 0,
            "current_groups_json": "{}",
        }
        payload = {
            "data": [],
            "user_rates": {},
            "refreshed_auth": {
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            },
        }
        with patch.object(app, "db_query_one", return_value=site), patch.object(
            app,
            "collect_site_groups",
            return_value=(True, {"default": {"ratio": 1}}, payload, "/api/v1/groups/available", None),
        ), patch.object(app, "get_last_success_snapshot", return_value=None), patch.object(
            app, "attach_group_model_names"
        ), patch.object(app, "notify_changes"), patch.object(
            app, "schedule_model_cache_refresh"
        ), patch.object(app, "db_execute", return_value=1) as execute:
            result = app.detect_site(7)

        self.assertTrue(result["success"])
        site_updates = [
            call.args[0]
            for call in execute.call_args_list
            if "UPDATE sites" in call.args[0]
        ]
        self.assertTrue(site_updates)
        self.assertTrue(
            all(
                field not in sql
                for sql in site_updates
                for field in (
                    "access_token =",
                    "refresh_token =",
                    "token_expires_at =",
                )
            )
        )

    def test_failed_detection_snapshot_redacts_internal_refreshed_auth(self):
        site = {
            "id": 7,
            "name": "sub2api",
            "platform": "sub2api",
            "base_url": "https://example.com",
            "auth_mode": "browser",
            "interval_minutes": 3,
            "consecutive_failures": 0,
        }
        payload = {
            "groups": {"status": 401},
            "refreshed_auth": {
                "access_token": "secret-rotated-access",
                "refresh_token": "secret-rotated-refresh",
            },
        }
        with patch.object(app, "db_query_one", return_value=site), patch.object(
            app,
            "collect_site_groups",
            return_value=(False, {}, payload, "/api/v1/groups/available", "HTTP 401"),
        ), patch.object(app, "get_last_success_snapshot", return_value=None), patch.object(
            app, "db_execute", return_value=1
        ) as execute:
            result = app.detect_site(7)

        self.assertFalse(result["success"])
        snapshot_insert = execute.call_args_list[0].args[1]
        self.assertNotIn("secret-rotated-access", repr(snapshot_insert))
        self.assertNotIn("secret-rotated-refresh", repr(snapshot_insert))
        self.assertNotIn("refreshed_auth", snapshot_insert[2])

    def test_sub2api_probe_redacts_internal_refreshed_auth(self):
        payload = {
            "groups": {"status": 401},
            "refreshed_auth": {
                "access_token": "secret-probe-access",
                "refresh_token": "secret-probe-refresh",
            },
        }
        with patch.object(
            app,
            "fetch_sub2api_user_groups",
            return_value=(False, payload, "HTTP 401"),
        ):
            result = app.probe_sub2api_groups("https://example.com", auth_mode="browser")

        self.assertFalse(result["success"])
        self.assertNotIn("secret-probe-access", repr(result))
        self.assertNotIn("secret-probe-refresh", repr(result))
        self.assertNotIn("refreshed_auth", result["raw"])

    def test_site_scoped_sub2api_reads_pass_site_id_for_session_persistence(self):
        site = {
            "id": 7,
            "base_url": "https://example.com",
            "platform": "sub2api",
            "login_enabled": 1,
            "auth_mode": "browser",
            "login_username": "user@example.com",
            "login_password": "saved-password",
            "access_token": "saved-access",
            "refresh_token": "saved-refresh",
            "current_groups_json": '{"default":{"ratio":1}}',
        }
        with patch.object(
            app,
            "fetch_sub2api_user_groups",
            return_value=(True, {"data": [], "user_rates": {}}, None),
        ) as groups:
            app.collect_site_groups(site)
        self.assertEqual(groups.call_args.kwargs["site_id"], 7)

        with patch.object(
            app,
            "fetch_sub2api_account",
            return_value=(False, {}, "account failed"),
        ) as account:
            app.build_site_account_payload(site)
        self.assertEqual(account.call_args.kwargs["site_id"], 7)

        with patch.object(
            app,
            "fetch_sub2api_model_data",
            return_value=(False, {}, "models failed"),
        ) as models:
            app.build_site_models_payload(site)
        self.assertEqual(models.call_args.kwargs["site_id"], 7)

    def test_browser_required_detection_keeps_previous_ratios_and_exposes_code(self):
        site = {
            "id": 7,
            "base_url": "https://example.com",
            "platform": "sub2api",
            "auth_mode": "browser",
            "interval_minutes": 3,
            "consecutive_failures": 0,
            "current_groups_json": '{"saved":{"ratio":0.3}}',
        }
        failure_payload = {
            "code": "BROWSER_SESSION_REQUIRED",
            "browser_sync_required": True,
        }
        with patch.object(
            app, "db_query_one", side_effect=[site, None]
        ), patch.object(
            app,
            "collect_site_groups",
            return_value=(
                False,
                {},
                failure_payload,
                "/api/v1/groups/available",
                "请先在浏览器登录并同步",
            ),
        ), patch.object(app, "db_execute", return_value=1) as execute:
            result = app.detect_site(7)

        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "BROWSER_SESSION_REQUIRED")
        self.assertTrue(result["browser_sync_required"])
        site_updates = [
            call.args[0]
            for call in execute.call_args_list
            if "UPDATE sites" in call.args[0]
        ]
        self.assertTrue(site_updates)
        self.assertTrue(all("current_groups_json" not in sql for sql in site_updates))

    def test_browser_first_migration_preserves_all_credentials_and_ratios(self):
        cursor = Mock()
        app.migrate_sub2api_sites_to_browser_first(cursor)

        self.assertEqual(cursor.execute.call_count, 2)
        auth_sql = cursor.execute.call_args_list[0].args[0]
        state_sql = cursor.execute.call_args_list[1].args[0]
        self.assertIn("SET auth_mode = 'browser'", auth_sql)
        self.assertIn("platform = 'sub2api'", auth_sql)
        self.assertIn("login_enabled = 1", auth_sql)
        self.assertIn("SET session_sync_status = 'ready'", state_sql)
        self.assertIn("session_sync_status = 'not_requested'", state_sql)
        self.assertIn("COALESCE(access_token, '') <> ''", state_sql)
        for protected_column in (
            "login_username",
            "login_password",
            "access_token",
            "refresh_token",
            "current_groups_json",
        ):
            self.assertNotIn(f"{protected_column} =", auth_sql)
            self.assertNotIn(f"{protected_column} =", state_sql)

    def test_browser_first_migration_runs_once_after_being_recorded(self):
        cursor = Mock()
        cursor.fetchone.return_value = {"name": app.SUB2API_BROWSER_FIRST_MIGRATION}

        applied = app.run_sub2api_browser_first_migration_once(cursor)

        self.assertFalse(applied)
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertFalse(any("UPDATE sites" in sql for sql in statements))

    def test_browser_first_migration_records_completion_after_first_run(self):
        cursor = Mock()
        cursor.fetchone.return_value = None

        applied = app.run_sub2api_browser_first_migration_once(cursor)

        self.assertTrue(applied)
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any("UPDATE sites" in sql for sql in statements))
        self.assertTrue(any("INSERT INTO app_schema_migrations" in sql for sql in statements))

    def test_missing_browser_token_returns_login_required_message(self):
        with patch.object(app, "sub2api_login") as login:
            ok, payload, error = app.fetch_sub2api_user_groups(
                "https://example.com", auth_mode="browser"
            )
        self.assertFalse(ok)
        self.assertEqual(error, "请先在浏览器登录并同步")
        self.assertEqual(payload["code"], "BROWSER_SESSION_REQUIRED")
        self.assertTrue(payload["browser_sync_required"])
        login.assert_not_called()

    def test_refresh_auth_failure_marks_browser_session_expired(self):
        site = {
            "id": 5,
            "base_url": "https://example.com",
            "platform": "sub2api",
            "auth_mode": "browser",
            "access_token": "expired-access",
            "refresh_token": "expired-refresh",
        }
        with patch.object(
            app,
            "fetch_sub2api_user_groups",
            return_value=(False, {"refresh": {"status": 401}}, "HTTP 401"),
        ), patch.object(app, "db_execute", return_value=1) as execute:
            ok, _groups, _payload, _source, error = app.collect_site_groups(site)

        self.assertFalse(ok)
        self.assertEqual(error, "HTTP 401")
        expiry_call = next(
            call for call in execute.call_args_list
            if "session_sync_status = 'expired'" in call.args[0]
        )
        self.assertEqual(expiry_call.args[1][-1], 5)

    def test_only_dynamic_completion_route_bypasses_console_auth(self):
        self.assertTrue(
            app.is_public_api_path(
                "/api/session-sync/requests/request_123/complete"
            )
        )
        self.assertFalse(
            app.is_public_api_path(
                "/api/session-sync/requests/request_123/complete/extra"
            )
        )
        self.assertFalse(
            app.is_public_api_path(
                "/api/sites/1/session-sync/requests/request_123"
            )
        )

    def test_site_sync_routes_create_poll_and_fail_requests(self):
        create_handler = object.__new__(app.Handler)
        create_handler.path = "/api/sites/7/session-sync/requests"
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app,
            "create_site_session_sync_request",
            return_value=(True, {"request_id": "request-id"}, None),
        ) as create, patch.object(app, "json_response") as response:
            app.Handler.do_POST(create_handler)
        create.assert_called_once_with(7)
        self.assertEqual(response.call_args.args[1]["data"]["request_id"], "request-id")

        poll_handler = object.__new__(app.Handler)
        poll_handler.path = "/api/sites/7/session-sync/requests/request-id"
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app,
            "get_site_session_sync_request",
            return_value={"request_id": "request-id", "status": "pending"},
        ) as poll, patch.object(app, "json_response") as response:
            app.Handler.do_GET(poll_handler)
        poll.assert_called_once_with(7, "request-id")
        self.assertEqual(response.call_args.args[1]["data"]["status"], "pending")

        fail_handler = object.__new__(app.Handler)
        fail_handler.path = "/api/sites/7/session-sync/requests/request-id/fail"
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value={"code": "EXTENSION_UNAVAILABLE"}
        ), patch.object(
            app, "fail_site_session_sync_request", return_value=(True, None)
        ) as fail, patch.object(app, "json_response") as response:
            app.Handler.do_POST(fail_handler)
        fail.assert_called_once_with(7, "request-id", "EXTENSION_UNAVAILABLE")
        self.assertTrue(response.call_args.args[1]["success"])

    def test_complete_request_rejects_origin_platform_and_oversize_fields(self):
        request = {
            "id": "request-id",
            "site_id": 7,
            "admin_site_id": None,
            "platform": "sub2api",
            "target_origin": "https://example.com",
            "status": "validating",
        }
        valid_body = {
            "status": "session_found",
            "platform": "sub2api",
            "observed_origin": "https://example.com",
            "session": {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "token_expires_at": "1785340000000",
            },
        }
        with patch.object(
            app, "claim_session_sync_request", return_value=(True, request, None)
        ), patch.object(app, "finish_session_sync_request") as finish, patch.object(
            app, "apply_sub2api_browser_session"
        ) as apply:
            status, payload = app.complete_session_sync_request(
                "request-id",
                "secret",
                {**valid_body, "observed_origin": "https://other.example"},
            )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "ORIGIN_MISMATCH")
        finish.assert_called_once()
        apply.assert_not_called()

        with patch.object(
            app, "claim_session_sync_request", return_value=(True, request, None)
        ), patch.object(app, "finish_session_sync_request"), patch.object(
            app, "apply_sub2api_browser_session"
        ) as apply:
            status, payload = app.complete_session_sync_request(
                "request-id", "secret", {**valid_body, "platform": "newapi"}
            )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "PLATFORM_MISMATCH")
        apply.assert_not_called()

        oversized = {
            **valid_body,
            "session": {**valid_body["session"], "access_token": "x" * 16385},
        }
        with patch.object(app, "claim_session_sync_request") as claim:
            status, payload = app.complete_session_sync_request(
                "request-id", "secret", oversized
            )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "SESSION_FIELD_TOO_LARGE")
        claim.assert_not_called()

    def test_complete_request_persists_and_returns_only_redacted_status(self):
        request = {
            "id": "request-id",
            "site_id": 7,
            "admin_site_id": None,
            "platform": "sub2api",
            "target_origin": "https://example.com",
            "status": "validating",
        }
        body = {
            "status": "session_found",
            "platform": "sub2api",
            "observed_origin": "https://example.com",
            "session": {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "token_expires_at": "1785340000000",
            },
        }
        with patch.object(
            app, "claim_session_sync_request", return_value=(True, request, None)
        ), patch.object(
            app, "apply_sub2api_browser_session", return_value=(True, None)
        ) as apply, patch.object(
            app, "finish_session_sync_request"
        ) as finish, patch.object(
            app, "detect_site", return_value={"success": True}
        ) as detect:
            status, payload = app.complete_session_sync_request(
                "request-id", "secret", body
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ready")
        self.assertNotIn("access-token", repr(payload))
        self.assertNotIn("refresh-token", repr(payload))
        apply.assert_called_once_with(
            7,
            "https://example.com",
            body["session"],
            request_id="request-id",
            expected_origin="https://example.com",
        )
        finish.assert_called_once_with("request-id", "ready")
        detect.assert_called_once_with(7)

    def test_complete_request_rejects_missing_wrong_and_replayed_secret(self):
        for claim_error, expected_status in (
            ("SYNC_REQUEST_SECRET_INVALID", 401),
            ("SYNC_REQUEST_CONSUMED", 409),
            ("SYNC_REQUEST_EXPIRED", 410),
        ):
            with self.subTest(claim_error=claim_error), patch.object(
                app,
                "claim_session_sync_request",
                return_value=(False, None, claim_error),
            ):
                status, payload = app.complete_session_sync_request(
                    "request-id",
                    "wrong",
                    {
                        "status": "no_session",
                        "platform": "sub2api",
                        "observed_origin": "https://example.com",
                    },
                )
            self.assertEqual(status, expected_status)
            self.assertEqual(payload["code"], claim_error)

        status, payload = app.complete_session_sync_request(
            "request-id", "", {"status": "no_session"}
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["code"], "SYNC_REQUEST_SECRET_REQUIRED")

    def test_create_sub2api_browser_site_does_not_require_tokens(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/sites"
        body = {
            "name": "Browser channel",
            "base_url": "https://example.com",
            "platform": "sub2api",
            "auth_mode": "browser",
            "login_enabled": True,
            "enabled": True,
            "interval_minutes": 3,
        }
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value=body
        ), patch.object(app, "db_execute", return_value=42) as execute, patch.object(
            app, "json_response"
        ) as response:
            app.Handler.do_POST(handler)

        insert = execute.call_args
        self.assertIn("INSERT INTO sites", insert.args[0])
        self.assertEqual(insert.args[1][6], "browser")
        self.assertEqual(insert.args[1][9], "")
        self.assertEqual(insert.args[1][11], "")
        self.assertEqual(response.call_args.args[1], {"success": True, "id": 42})

    def test_create_sub2api_browser_site_saves_optional_password_fallback(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/sites"
        body = {
            "name": "Browser channel",
            "base_url": "https://example.com",
            "platform": "sub2api",
            "auth_mode": "browser",
            "login_enabled": True,
            "login_username": "user@example.com",
            "login_password": "saved-password",
        }
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value=body
        ), patch.object(app, "db_execute", return_value=42) as execute, patch.object(
            app, "json_response"
        ):
            app.Handler.do_POST(handler)

        params = execute.call_args.args[1]
        self.assertEqual(params[7], "user@example.com")
        self.assertEqual(params[8], "saved-password")

    def test_site_summary_exposes_session_sync_state_without_tokens(self):
        site = {
            "id": 8,
            "name": "Browser channel",
            "base_url": "https://example.com",
            "platform": "sub2api",
            "enabled": 1,
            "interval_minutes": 3,
            "login_enabled": 1,
            "auth_mode": "browser",
            "access_token": "secret-access",
            "refresh_token": "secret-refresh",
            "session_sync_status": "ready",
            "session_sync_error": None,
            "session_synced_at": "2026-07-30T10:00:00+08:00",
            "status": "ok",
            "last_error": None,
            "last_check_at": None,
            "next_check_at": None,
            "consecutive_failures": 0,
        }
        with patch.object(app, "db_query_one", return_value=None):
            payload = app.site_summary(site)
        self.assertEqual(payload["session_sync_status"], "ready")
        self.assertEqual(
            payload["session_synced_at"], "2026-07-30T10:00:00+08:00"
        )
        self.assertNotIn("secret-access", repr(payload))
        self.assertNotIn("secret-refresh", repr(payload))

    def test_update_sub2api_site_can_switch_to_browser_mode_without_tokens(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/sites/8"
        existing = {
            "id": 8,
            "platform": "sub2api",
            "auth_mode": "password",
            "access_token": "",
            "refresh_token": "",
            "login_username": "old@example.com",
            "login_password": "old-password",
        }
        body = {
            "platform": "sub2api",
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
        self.assertEqual(response.call_args.args[1], {"success": True})

    def test_update_sub2api_site_switching_to_browser_preserves_saved_fallbacks(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/sites/8"
        existing = {
            "id": 8,
            "platform": "sub2api",
            "auth_mode": "password",
            "access_token": "saved-access",
            "refresh_token": "saved-refresh",
            "token_expires_at": "2026-08-01T00:00:00+08:00",
            "login_username": "old@example.com",
            "login_password": "old-password",
        }
        body = {
            "platform": "sub2api",
            "login_enabled": True,
            "auth_mode": "browser",
        }
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value=body
        ), patch.object(app, "db_query_one", return_value=existing), patch.object(
            app, "db_execute", return_value=1
        ) as execute, patch.object(app, "invalidate_site_model_cache"), patch.object(
            app, "schedule_model_cache_refresh"
        ), patch.object(app, "json_response"):
            app.Handler.do_PUT(handler)

        sql = execute.call_args.args[0]
        for protected_column in (
            "login_username",
            "login_password",
            "access_token",
            "refresh_token",
            "token_expires_at",
        ):
            self.assertNotIn(f"{protected_column} = ?", sql)

    def test_update_browser_site_can_replace_optional_password_fallback(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/sites/8"
        existing = {
            "id": 8,
            "platform": "sub2api",
            "auth_mode": "browser",
            "login_username": "old@example.com",
            "login_password": "old-password",
            "access_token": "saved-access",
            "refresh_token": "saved-refresh",
        }
        body = {
            "platform": "sub2api",
            "login_enabled": True,
            "auth_mode": "browser",
            "login_username": "new@example.com",
            "login_password": "new-password",
        }
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value=body
        ), patch.object(app, "db_query_one", return_value=existing), patch.object(
            app, "db_execute", return_value=1
        ) as execute, patch.object(app, "invalidate_site_model_cache"), patch.object(
            app, "schedule_model_cache_refresh"
        ), patch.object(app, "json_response"):
            app.Handler.do_PUT(handler)

        sql, params = execute.call_args.args
        self.assertIn("login_username = ?", sql)
        self.assertIn("login_password = ?", sql)
        self.assertIn("new@example.com", params)
        self.assertIn("new-password", params)

    def test_create_admin_request_binds_only_admin_target(self):
        admin_site = {
            "id": 13,
            "base_url": "https://newapi.example/dashboard",
            "platform": "newapi",
        }
        with patch.object(app, "db_query_one", return_value=admin_site), patch.object(
            app, "db_execute", return_value=1
        ) as execute, patch.object(
            app.secrets,
            "token_urlsafe",
            side_effect=["admin-request", "admin-secret"],
        ):
            ok, payload, error = app.create_admin_site_session_sync_request(13)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["target_kind"], "admin_site")
        insert = next(
            call for call in execute.call_args_list
            if "INSERT INTO browser_session_sync_requests" in call.args[0]
        )
        self.assertIsNone(insert.args[1][1])
        self.assertEqual(insert.args[1][2], 13)
        self.assertEqual(insert.args[1][3], "newapi")

    def test_admin_status_lookup_is_separate_from_site_target(self):
        row = {
            "id": "admin-request",
            "site_id": None,
            "admin_site_id": 13,
            "platform": "newapi",
            "target_origin": "https://newapi.example",
            "status": "pending",
            "expires_at": (app.app_now() + timedelta(seconds=30)).isoformat(),
        }
        with patch.object(app, "db_query_one", return_value=row) as query:
            payload = app.get_admin_site_session_sync_request(13, "admin-request")
        self.assertEqual(payload["target_kind"], "admin_site")
        self.assertIn("admin_site_id = ?", query.call_args.args[0])
        self.assertIn("site_id IS NULL", query.call_args.args[0])

    def test_claimed_request_retains_exact_target_kind(self):
        row = {
            "id": "admin-request",
            "site_id": None,
            "admin_site_id": 13,
            "platform": "newapi",
            "target_origin": "https://newapi.example",
            "secret_hash": app.hash_session_sync_secret("secret"),
            "status": "pending",
            "expires_at": (app.app_now() + timedelta(seconds=30)).isoformat(),
        }
        with patch.object(app, "db_query_one", return_value=row), patch.object(
            app, "db_execute", return_value=1
        ):
            ok, claimed, error = app.claim_session_sync_request(
                "admin-request", "secret"
            )
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(app.session_sync_target_kind(claimed), "admin_site")
        self.assertIsNone(claimed["site_id"])
        self.assertEqual(claimed["admin_site_id"], 13)

    def test_admin_sync_routes_do_not_reuse_site_routes(self):
        create_handler = object.__new__(app.Handler)
        create_handler.path = "/api/admin/sites/13/session-sync/requests"
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app,
            "create_admin_site_session_sync_request",
            return_value=(
                True,
                {"request_id": "admin-request", "target_kind": "admin_site"},
                None,
            ),
        ) as create, patch.object(app, "create_site_session_sync_request") as site_create, patch.object(
            app, "json_response"
        ) as response:
            app.Handler.do_POST(create_handler)
        create.assert_called_once_with(13)
        site_create.assert_not_called()
        self.assertEqual(response.call_args.args[1]["data"]["target_kind"], "admin_site")

        poll_handler = object.__new__(app.Handler)
        poll_handler.path = "/api/admin/sites/13/session-sync/requests/admin-request"
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app,
            "get_admin_site_session_sync_request",
            return_value={"request_id": "admin-request", "status": "pending"},
        ) as poll, patch.object(app, "get_site_session_sync_request") as site_poll, patch.object(
            app, "json_response"
        ):
            app.Handler.do_GET(poll_handler)
        poll.assert_called_once_with(13, "admin-request")
        site_poll.assert_not_called()

        fail_handler = object.__new__(app.Handler)
        fail_handler.path = "/api/admin/sites/13/session-sync/requests/admin-request/fail"
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value={"code": "EXTENSION_UNAVAILABLE"}
        ), patch.object(
            app, "fail_admin_site_session_sync_request", return_value=(True, None)
        ) as fail, patch.object(
            app, "fail_site_session_sync_request"
        ) as site_fail, patch.object(
            app, "json_response"
        ) as response:
            app.Handler.do_POST(fail_handler)
        fail.assert_called_once_with(
            13, "admin-request", "EXTENSION_UNAVAILABLE"
        )
        site_fail.assert_not_called()
        self.assertTrue(response.call_args.args[1]["success"])

    def test_admin_page_failure_is_bound_to_admin_target(self):
        row = {
            "id": "admin-request",
            "site_id": None,
            "admin_site_id": 13,
            "status": "pending",
            "expires_at": (app.app_now() + timedelta(seconds=30)).isoformat(),
        }
        with patch.object(app, "db_query_one", return_value=row) as query, patch.object(
            app, "finish_session_sync_request"
        ) as finish:
            ok, error = app.fail_admin_site_session_sync_request(
                13, "admin-request", "EXTENSION_UNAVAILABLE"
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertIn("admin_site_id = ?", query.call_args.args[0])
        self.assertIn("site_id IS NULL", query.call_args.args[0])
        finish.assert_called_once_with(
            "admin-request",
            "extension_unavailable",
            "EXTENSION_UNAVAILABLE",
            "未安装或未连接浏览器同步扩展",
        )

    def test_finishing_admin_request_updates_only_browser_login_state(self):
        with patch.object(
            app,
            "db_query_one",
            side_effect=[
                {"site_id": None, "admin_site_id": 13, "platform": "newapi",
                 "target_origin": "https://newapi.example", "status": "validating"},
                None,
            ],
        ), patch.object(app, "db_execute_rowcount", return_value=1) as execute_rowcount:
            finished = app.finish_session_sync_request(
                "admin-request", "failed", "NO_SESSION", "没有登录态，请提前登录"
            )

        self.assertTrue(finished)
        admin_update = next(
            call
            for call in execute_rowcount.call_args_list
            if "UPDATE admin_sites" in call.args[0]
        )
        self.assertIn("browser_login_last_error", admin_update.args[0])
        self.assertIn("browser_login_last_check_at", admin_update.args[0])
        self.assertNotIn("access_token", admin_update.args[0])
        self.assertNotIn("security_proof", admin_update.args[0])

    def test_expired_admin_claim_records_only_browser_login_error(self):
        row = {
            "id": "admin-request",
            "site_id": None,
            "admin_site_id": 13,
            "secret_hash": app.hash_session_sync_secret("secret"),
            "status": "pending",
            "expires_at": (app.app_now() - timedelta(seconds=1)).isoformat(),
        }
        with patch.object(app, "db_query_one", return_value=row), patch.object(
            app, "db_execute", return_value=1
        ) as execute:
            ok, claimed, error = app.claim_session_sync_request(
                "admin-request", "secret"
            )

        self.assertFalse(ok)
        self.assertIsNone(claimed)
        self.assertEqual(error, "SYNC_REQUEST_EXPIRED")
        admin_update = next(
            call
            for call in execute.call_args_list
            if "UPDATE admin_sites" in call.args[0]
        )
        self.assertIn("browser_login_last_error", admin_update.args[0])
        self.assertNotIn("access_token", admin_update.args[0])
        self.assertNotIn("security_proof", admin_update.args[0])


if __name__ == "__main__":
    unittest.main()
