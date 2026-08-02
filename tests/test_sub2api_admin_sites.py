import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import app


class Sub2ApiAdminSiteTests(unittest.TestCase):
    def test_admin_site_schema_additions_are_backward_compatible(self):
        self.assertEqual(
            app.ADMIN_SITE_COLUMN_ADDITIONS["platform"],
            "VARCHAR(32) NOT NULL DEFAULT 'newapi'",
        )
        self.assertEqual(app.ADMIN_SITE_COLUMN_ADDITIONS["sub2api_access_token"], "TEXT")
        self.assertEqual(app.ADMIN_SITE_COLUMN_ADDITIONS["sub2api_refresh_token"], "TEXT")
        self.assertEqual(app.ADMIN_SITE_COLUMN_ADDITIONS["sub2api_access_expires_at"], "BIGINT")

    def test_admin_site_platform_defaults_to_newapi(self):
        self.assertEqual(app.admin_site_platform({}), "newapi")
        self.assertEqual(app.admin_site_platform({"platform": "sub2api"}), "sub2api")

    def test_admin_site_base_url_rejects_userinfo_and_non_http_schemes(self):
        self.assertEqual(
            app.validate_admin_site_base_url("https://sub.example"),
            ("https://sub.example", None),
        )
        self.assertIsNotNone(
            app.validate_admin_site_base_url("https://user:pass@sub.example")[1]
        )
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
        with patch.object(
            app, "admin_request_json", return_value=(True, login_payload, None)
        ) as request:
            ok, auth, error = app.sub2api_admin_login(
                "https://sub.example", "user@example.com", "password"
            )
        self.assertFalse(ok)
        self.assertEqual(auth, {})
        self.assertIn("无主站管理权限", error)
        self.assertEqual(request.call_args.kwargs["payload"]["turnstile_token"], "")

    def test_sub2api_admin_login_rejects_two_factor_flow(self):
        login_payload = {
            "code": 0,
            "data": {"requires_2fa": True, "temp_token": "temporary"},
        }
        with patch.object(
            app, "admin_request_json", return_value=(True, login_payload, None)
        ):
            ok, auth, error = app.sub2api_admin_login(
                "https://sub.example", "admin@example.com", "password"
            )
        self.assertFalse(ok)
        self.assertEqual(auth, {})
        self.assertIn("2FA", error)

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
        with patch.object(
            app, "admin_request_json", return_value=(True, login_payload, None)
        ):
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
        with patch.object(app, "db_query_one", return_value=site), patch.object(
            app,
            "sub2api_admin_refresh_token",
            return_value=(True, refreshed, None),
        ) as refresh, patch.object(app, "db_execute") as save:
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
        auth = {
            "access_token": "login-access",
            "refresh_token": "login-refresh",
            "access_expires_at": 9999999999,
        }
        with patch.object(app, "db_query_one", return_value=site), patch.object(
            app,
            "sub2api_admin_refresh_token",
            return_value=(False, {}, "expired"),
        ), patch.object(
            app, "sub2api_admin_login", return_value=(True, auth, None)
        ) as login, patch.object(app, "db_execute"):
            ok, token, error = app.ensure_sub2api_admin_session(site)
        self.assertTrue(ok)
        self.assertEqual(token, "login-access")
        self.assertIsNone(error)
        login.assert_called_once()

    def test_concurrent_forced_refresh_reuses_token_rotated_by_first_caller(self):
        site = {
            "id": 5005,
            "platform": "sub2api",
            "base_url": "https://sub.example",
            "login_username": "admin@example.com",
            "login_password": "password",
            "sub2api_access_token": "old-access",
            "sub2api_refresh_token": "old-refresh",
            "sub2api_access_expires_at": 9999999999,
        }
        saved = dict(site)
        refresh_calls = []
        results = []

        def refresh(_base_url, _refresh_token):
            refresh_calls.append(True)
            time.sleep(0.05)
            return True, {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            }, None

        def persist(_site_id, auth):
            saved.update(
                {
                    "sub2api_access_token": auth["access_token"],
                    "sub2api_refresh_token": auth["refresh_token"],
                    "sub2api_access_expires_at": auth["access_expires_at"],
                }
            )

        def worker():
            results.append(
                app.ensure_sub2api_admin_session(
                    site,
                    force_refresh=True,
                    rejected_access_token="old-access",
                )
            )

        with patch.object(app, "db_query_one", side_effect=lambda *_: dict(saved)), patch.object(
            app, "sub2api_admin_refresh_token", side_effect=refresh
        ), patch.object(app, "_persist_sub2api_admin_auth", side_effect=persist):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(len(refresh_calls), 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item[0] for item in results))
        self.assertEqual({item[1] for item in results}, {"new-access"})

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

    def test_admin_redirect_handler_allows_same_origin_default_port(self):
        handler = app.SameOriginAdminRedirectHandler()
        request = urllib.request.Request(
            "https://sub.example/api/v1/admin/channels",
            headers={"Authorization": "Bearer secret"},
        )
        redirected = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://sub.example:443/api/v1/admin/channels?page=2",
        )
        self.assertEqual(redirected.full_url, "https://sub.example:443/api/v1/admin/channels?page=2")

    def test_create_sub2api_admin_site_logs_in_before_insert(self):
        auth = {
            "access_token": "access",
            "refresh_token": "refresh",
            "access_expires_at": 9999999999,
        }
        body = {
            "name": "sub main",
            "platform": "sub2api",
            "base_url": "https://sub.example",
            "login_username": "admin@example.com",
            "login_password": "password",
        }
        with patch.object(
            app, "sub2api_admin_login", return_value=(True, auth, None)
        ) as login, patch.object(app, "db_execute", return_value=17) as execute:
            ok, site_id, error = app.create_admin_site(body)
        self.assertTrue(ok)
        self.assertEqual(site_id, 17)
        self.assertIsNone(error)
        login.assert_called_once_with(
            "https://sub.example", "admin@example.com", "password"
        )
        sql, params = execute.call_args.args
        self.assertIn("platform", sql)
        self.assertIn("sub2api", params)
        self.assertIn("refresh", params)

    def test_update_rejects_platform_change(self):
        existing = {
            "id": 3,
            "platform": "newapi",
            "name": "main",
            "base_url": "https://new.example",
        }
        with patch.object(app, "db_query_one", return_value=existing), patch.object(
            app, "db_execute"
        ) as execute:
            ok, error = app.update_admin_site(3, {"platform": "sub2api"})
        self.assertFalse(ok)
        self.assertIn("不可修改", error)
        execute.assert_not_called()

    def test_update_sub2api_credentials_rotates_saved_session(self):
        existing = {
            "id": 3,
            "platform": "sub2api",
            "name": "main",
            "base_url": "https://sub.example",
            "login_username": "old@example.com",
            "login_password": "old-password",
        }
        auth = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "access_expires_at": 9999999999,
        }
        with patch.object(app, "db_query_one", return_value=existing), patch.object(
            app, "sub2api_admin_login", return_value=(True, auth, None)
        ) as login, patch.object(app, "db_execute") as execute:
            ok, error = app.update_admin_site(
                3,
                {
                    "login_username": "new@example.com",
                    "login_password": "new-password",
                },
            )
        self.assertTrue(ok)
        self.assertIsNone(error)
        login.assert_called_once_with(
            "https://sub.example", "new@example.com", "new-password"
        )
        sql, params = execute.call_args.args
        self.assertIn("sub2api_refresh_token", sql)
        self.assertIn("new-refresh", params)

    def test_connection_test_reports_sub2api_channel_count(self):
        body = {
            "platform": "sub2api",
            "base_url": "https://sub.example",
            "login_username": "admin@example.com",
            "login_password": "password",
        }
        auth = {
            "access_token": "access",
            "refresh_token": "refresh",
            "access_expires_at": 9999999999,
        }
        with patch.object(
            app, "sub2api_admin_login", return_value=(True, auth, None)
        ), patch.object(
            app,
            "fetch_sub2api_admin_channels_by_token",
            return_value=(True, [{"id": 1}, {"id": 2}], {}, None),
            create=True,
        ):
            ok, payload, error = app.test_admin_site_connection(body)
        self.assertTrue(ok)
        self.assertEqual(payload["channels_count"], 2)
        self.assertEqual(payload["platform"], "sub2api")
        self.assertIsNone(error)

    def test_connection_test_reuses_saved_password_during_edit(self):
        saved = {
            "id": 8,
            "platform": "sub2api",
            "base_url": "https://sub.example",
            "login_username": "admin@example.com",
            "login_password": "saved-password",
        }
        auth = {
            "access_token": "access",
            "refresh_token": "refresh",
            "access_expires_at": 9999999999,
        }
        with patch.object(app, "db_query_one", return_value=saved), patch.object(
            app, "sub2api_admin_login", return_value=(True, auth, None)
        ) as login, patch.object(
            app,
            "fetch_sub2api_admin_channels_by_token",
            return_value=(True, [], {}, None),
            create=True,
        ):
            ok, payload, error = app.test_admin_site_connection(
                {
                    "admin_site_id": 8,
                    "platform": "sub2api",
                    "base_url": "https://sub.example",
                    "login_username": "admin@example.com",
                    "login_password": "",
                }
            )
        self.assertTrue(ok)
        self.assertEqual(payload["channels_count"], 0)
        self.assertIsNone(error)
        login.assert_called_once_with(
            "https://sub.example", "admin@example.com", "saved-password"
        )

    def test_connection_test_preserves_sub2api_upstream_channel_error(self):
        auth = {
            "access_token": "access",
            "refresh_token": "refresh",
            "access_expires_at": 9999999999,
        }
        upstream = {
            "status": 503,
            "raw": '{"code":"MAINTENANCE","message":"try later"}',
        }
        with patch.object(
            app, "sub2api_admin_login", return_value=(True, auth, None)
        ), patch.object(
            app,
            "fetch_sub2api_admin_channels_by_token",
            return_value=(False, [], upstream, "HTTP 503"),
        ):
            ok, payload, error = app.test_admin_site_connection(
                {
                    "platform": "sub2api",
                    "base_url": "https://sub.example",
                    "login_username": "admin@example.com",
                    "login_password": "password",
                }
            )

        self.assertFalse(ok)
        self.assertEqual(payload["error_source"], "upstream")
        self.assertEqual(payload["details"], upstream)
        self.assertEqual(error, "HTTP 503")

    def test_newapi_connection_test_uses_saved_admin_token(self):
        saved = {
            "id": 4,
            "platform": "newapi",
            "base_url": "https://new.example",
            "access_token": "saved-access",
            "access_user_id": "11",
        }
        groups_payload = {
            "success": True,
            "data": {"default": {"ratio": 1}, "vip": {"ratio": 0.8}},
        }
        with patch.object(app, "db_query_one", return_value=saved), patch.object(
            app,
            "fetch_newapi_groups_with_access_token",
            return_value=(True, groups_payload, None),
        ) as fetch:
            ok, payload, error = app.test_admin_site_connection(
                {"admin_site_id": 4, "platform": "newapi"}
            )
        self.assertTrue(ok)
        self.assertEqual(payload["groups_count"], 2)
        self.assertIsNone(error)
        fetch.assert_called_once_with(
            "https://new.example", "saved-access", "11"
        )

    def test_sub2api_admin_channel_adapter_reads_all_pages_and_never_accounts(self):
        responses = [
            (
                True,
                {
                    "code": 0,
                    "data": {
                        "items": [{"id": 1}],
                        "total": 2,
                        "page": 1,
                        "page_size": 1,
                    },
                },
                None,
            ),
            (
                True,
                {
                    "code": 0,
                    "data": {
                        "items": [{"id": 2}],
                        "total": 2,
                        "page": 2,
                        "page_size": 1,
                    },
                },
                None,
            ),
        ]
        with patch.object(app, "admin_request_json", side_effect=responses) as request:
            ok, channels, upstream, error = app.fetch_sub2api_admin_channels_by_token(
                "https://sub.example", "access", page_size=1
            )
        self.assertTrue(ok)
        self.assertEqual([item["id"] for item in channels], [1, 2])
        self.assertEqual(upstream, {})
        self.assertIsNone(error)
        urls = [call.args[0] for call in request.call_args_list]
        self.assertTrue(all("/api/v1/admin/channels" in url for url in urls))
        self.assertTrue(all("/api/v1/admin/accounts" not in url for url in urls))

    def test_sub2api_admin_channel_adapter_rejects_truncated_pagination(self):
        page = {
            "code": 0,
            "data": {"items": [{"id": 1}], "total": 5, "page_size": 1},
        }
        with patch.object(
            app, "admin_request_json", return_value=(True, page, None)
        ):
            ok, channels, upstream, error = app.fetch_sub2api_admin_channels_by_token(
                "https://sub.example", "access", page_size=1, max_pages=2
            )
        self.assertFalse(ok)
        self.assertEqual(channels, [])
        self.assertEqual(upstream, {})
        self.assertIn("截断", error)

    def test_sub2api_read_adapters_preserve_structured_upstream_errors(self):
        site = {"id": 5, "platform": "sub2api", "base_url": "https://sub.example"}
        group_error = {
            "status": 429,
            "raw": '{"code":"RATE_LIMITED","message":"too many requests"}',
        }
        with patch.object(
            app,
            "sub2api_admin_request",
            return_value=(False, group_error, "HTTP 429"),
        ):
            ok, groups, upstream, error = app.fetch_sub2api_admin_groups(site)
        self.assertFalse(ok)
        self.assertEqual(groups, [])
        self.assertEqual(upstream, group_error)
        self.assertEqual(error, "HTTP 429")

        channel_error = {
            "status": 503,
            "raw": '{"code":"MAINTENANCE","message":"try later"}',
        }
        with patch.object(
            app,
            "fetch_sub2api_admin_groups",
            return_value=(True, [], {}, None),
        ), patch.object(
            app,
            "sub2api_admin_request",
            return_value=(False, channel_error, "HTTP 503"),
        ):
            ok, channels, meta, error = app.fetch_sub2api_admin_site_channels(site)
        self.assertFalse(ok)
        self.assertEqual(channels, [])
        self.assertEqual(meta, channel_error)
        self.assertEqual(error, "HTTP 503")

        semantic_error = {
            "code": 422,
            "message": "model pricing intervals overlap",
        }
        with patch.object(
            app,
            "fetch_sub2api_admin_groups",
            return_value=(True, [], {}, None),
        ), patch.object(
            app,
            "sub2api_admin_request",
            return_value=(True, semantic_error, None),
        ):
            ok, channels, meta, error = app.fetch_sub2api_admin_site_channels(site)
        self.assertFalse(ok)
        self.assertEqual(channels, [])
        self.assertEqual(meta, semantic_error)
        self.assertIn("model pricing", error)

    def test_sub2api_detail_and_update_preserve_structured_upstream_errors(self):
        site = {"id": 5, "platform": "sub2api", "base_url": "https://sub.example"}
        upstream_error = {
            "status": 404,
            "raw": '{"code":"NOT_FOUND","message":"channel not found"}',
        }
        with patch.object(
            app,
            "sub2api_admin_request",
            return_value=(False, upstream_error, "HTTP 404"),
        ):
            detail_ok, detail_payload, detail_error = (
                app.fetch_sub2api_admin_channel_detail(site, 7)
            )
            update_ok, update_payload, update_error = (
                app.update_sub2api_admin_channel(site, 7, {"status": "disabled"})
            )

        self.assertFalse(detail_ok)
        self.assertEqual(detail_payload, upstream_error)
        self.assertEqual(detail_error, "HTTP 404")
        self.assertFalse(update_ok)
        self.assertEqual(update_payload, upstream_error)
        self.assertEqual(update_error, "HTTP 404")

    def test_sub2api_admin_request_refreshes_once_after_unauthorized(self):
        site = {"id": 5, "platform": "sub2api", "base_url": "https://sub.example"}
        with patch.object(
            app,
            "ensure_sub2api_admin_session",
            side_effect=[(True, "old-access", None), (True, "new-access", None)],
        ) as ensure, patch.object(
            app,
            "admin_request_json",
            side_effect=[
                (False, {"status": 401}, "HTTP 401"),
                (True, {"code": 0, "data": {"items": []}}, None),
            ],
        ) as request:
            ok, payload, error = app.sub2api_admin_request(
                site, "/api/v1/admin/channels?page=1&page_size=100"
            )
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["code"], 0)
        self.assertEqual(ensure.call_count, 2)
        self.assertTrue(ensure.call_args_list[1].kwargs["force_refresh"])
        self.assertEqual(
            request.call_args_list[1].kwargs["headers"]["Authorization"],
            "Bearer new-access",
        )

    def test_sub2api_admin_request_does_not_replay_put_after_validation_error(self):
        site = {"id": 5, "platform": "sub2api", "base_url": "https://sub.example"}
        validation_payload = {
            "status": 400,
            "raw": '{"code":400,"message":"max_tokens must be greater than min_tokens"}',
        }
        with patch.object(
            app,
            "ensure_sub2api_admin_session",
            return_value=(True, "access", None),
        ) as ensure, patch.object(
            app,
            "admin_request_json",
            return_value=(False, validation_payload, "HTTP 400"),
        ) as request:
            ok, payload, error = app.sub2api_admin_request(
                site,
                "/api/v1/admin/channels/7",
                method="PUT",
                payload={"model_pricing": []},
            )

        self.assertFalse(ok)
        self.assertEqual(payload, validation_payload)
        self.assertEqual(error, "HTTP 400")
        ensure.assert_called_once_with(site)
        request.assert_called_once()

    def test_sub2api_proxy_error_envelope_classifies_and_redacts_upstream_failures(self):
        cases = [
            (
                {
                    "status": 400,
                    "raw": (
                        '{"code":"INVALID_REQUEST","message":'
                        '"max_tokens invalid; access_token=top-secret"}'
                    ),
                },
                "HTTP 400",
                400,
                "validation",
                400,
                "INVALID_REQUEST",
            ),
            (
                {"status": 404, "raw": '{"message":"channel not found"}'},
                "HTTP 404",
                404,
                "not_found",
                404,
                "",
            ),
            (
                {"status": 429, "raw": '{"message":"too many requests"}'},
                "HTTP 429",
                429,
                "rate_limited",
                429,
                "",
            ),
            (
                {"status": 503, "raw": '{"message":"maintenance"}'},
                "HTTP 503",
                502,
                "upstream_server",
                503,
                "",
            ),
            (
                {"error": "<urlopen error timed out>"},
                "<urlopen error timed out>",
                502,
                "transport",
                0,
                "",
            ),
        ]

        for payload, error, expected_status, category, upstream_status, code in cases:
            with self.subTest(category=category):
                status, response = app.sub2api_proxy_error_response(payload, error)
                self.assertEqual(status, expected_status)
                self.assertFalse(response["success"])
                self.assertEqual(response["category"], category)
                self.assertNotIn("raw", response)
                self.assertNotIn("top-secret", str(response))
                if upstream_status:
                    self.assertEqual(response["upstream_status"], upstream_status)
                else:
                    self.assertNotIn("upstream_status", response)
                if code:
                    self.assertEqual(response["upstream_code"], code)
                else:
                    self.assertNotIn("upstream_code", response)

    def test_sub2api_admin_request_rejects_account_pool_path(self):
        site = {"id": 5, "platform": "sub2api", "base_url": "https://sub.example"}
        with patch.object(app, "ensure_sub2api_admin_session") as ensure, patch.object(
            app, "admin_request_json"
        ) as request:
            ok, payload, error = app.sub2api_admin_request(
                site, "/api/v1/admin/accounts?page=1"
            )
        self.assertFalse(ok)
        self.assertEqual(payload, {})
        self.assertIn("号池", error)
        ensure.assert_not_called()
        request.assert_not_called()

    def test_sub2api_admin_request_rejects_similar_channel_prefix(self):
        site = {"id": 5, "platform": "sub2api", "base_url": "https://sub.example"}
        with patch.object(app, "ensure_sub2api_admin_session") as ensure, patch.object(
            app, "admin_request_json"
        ) as request:
            ok, payload, error = app.sub2api_admin_request(
                site, "/api/v1/admin/channels-export"
            )
        self.assertFalse(ok)
        self.assertEqual(payload, {})
        self.assertIn("仅允许访问渠道和分组配置", error)
        ensure.assert_not_called()
        request.assert_not_called()

    def test_normalized_sub2api_channel_contains_group_rates_and_pricing(self):
        channel = {
            "id": 7,
            "name": "Claude",
            "status": "active",
            "group_ids": [2],
            "model_pricing": [
                {
                    "platform": "anthropic",
                    "models": ["claude-sonnet-4"],
                    "billing_mode": "token",
                }
            ],
        }
        groups = {
            2: {
                "id": 2,
                "name": "高级组",
                "rate_multiplier": 0.8,
                "platform": "anthropic",
                "status": "active",
            }
        }
        result = app.normalize_sub2api_admin_channel(channel, groups)
        self.assertEqual(result["source_platform"], "sub2api")
        self.assertEqual(result["normalized_status"], "active")
        self.assertEqual(result["groups"][0]["rate_multiplier"], 0.8)
        self.assertEqual(
            result["model_pricing"][0]["models"], ["claude-sonnet-4"]
        )

    def test_normalized_sub2api_error_status_stays_error(self):
        result = app.normalize_sub2api_admin_channel(
            {"id": 7, "status": "error", "group_ids": []}, {}
        )
        self.assertEqual(result["normalized_status"], "error")

    def test_sub2api_channel_patch_accepts_only_upstream_billing_model_sources(self):
        for source in ("requested", "upstream", "channel_mapped"):
            with self.subTest(source=source):
                self.assertIsNone(
                    app.validate_sub2api_admin_channel_patch(
                        {"billing_model_source": source}
                    )
                )

        for source in ("channel", "group", "unknown"):
            with self.subTest(source=source):
                error = app.validate_sub2api_admin_channel_patch(
                    {"billing_model_source": source}
                )
                self.assertIsNotNone(error)
                self.assertIn("billing_model_source", error)

    def test_sub2api_group_payload_preserves_identity_and_multiplier(self):
        payload = app.sub2api_admin_groups_payload(
            [
                {
                    "id": 2,
                    "name": "高级组",
                    "description": "Claude 渠道",
                    "rate_multiplier": 0.8,
                    "platform": "anthropic",
                    "status": "active",
                }
            ]
        )
        group = payload["data"]["高级组"]
        self.assertEqual(group["id"], 2)
        self.assertEqual(group["name"], "高级组")
        self.assertEqual(group["ratio"], 0.8)
        self.assertEqual(group["rate_multiplier"], 0.8)

    def test_sub2api_channel_update_rejects_unknown_fields_and_preserves_empty_values(self):
        site = {"id": 5, "platform": "sub2api", "base_url": "https://sub.example"}
        with patch.object(
            app,
            "sub2api_admin_request",
            return_value=(True, {"code": 0, "data": {"id": 7}}, None),
        ) as request:
            ok, payload, error = app.update_sub2api_admin_channel(
                site, 7, {"group_ids": [], "model_mapping": {}}
            )
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(request.call_args.kwargs["payload"]["group_ids"], [])
        self.assertEqual(request.call_args.kwargs["payload"]["model_mapping"], {})

        with patch.object(app, "sub2api_admin_request") as rejected_request:
            ok, payload, error = app.update_sub2api_admin_channel(
                site, 7, {"password": "leak"}
            )
        self.assertFalse(ok)
        self.assertEqual(payload, {})
        self.assertIn("password", error)
        rejected_request.assert_not_called()

    def test_unified_channel_dispatch_uses_sub2api_adapter(self):
        site = {"id": 5, "platform": "sub2api"}
        with patch.object(
            app,
            "fetch_sub2api_admin_site_channels",
            return_value=(True, [{"id": 1}], {"total": 1}, None),
        ) as sub, patch.object(app, "fetch_all_newapi_channels") as newapi:
            result = app.fetch_admin_site_channels(site, "claude")
        self.assertEqual(result[1][0]["id"], 1)
        sub.assert_called_once_with(site, "claude")
        newapi.assert_not_called()

    def test_newapi_dispatch_keeps_existing_adapter(self):
        site = {"id": 2, "platform": "newapi"}
        with patch.object(
            app,
            "fetch_all_newapi_channels",
            return_value=(True, [{"id": 9}], None),
        ) as newapi:
            ok, items, meta, error = app.fetch_admin_site_channels(site, "")
        self.assertTrue(ok)
        self.assertEqual(items[0]["id"], 9)
        self.assertEqual(meta["total"], 1)
        self.assertIsNone(error)
        newapi.assert_called_once_with(site)

    def test_unified_group_dispatch_preserves_record_contract(self):
        site = {"id": 5, "platform": "sub2api"}
        groups = [{"id": 2, "name": "高级组", "rate_multiplier": 0.8}]
        with patch.object(
            app, "fetch_sub2api_admin_groups", return_value=(True, groups, {}, None)
        ):
            ok, payload, error = app.fetch_admin_site_groups(site)
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["data"]["高级组"]["rate_multiplier"], 0.8)

    def test_get_admin_site_accepts_sub2api_credentials(self):
        site = {
            "id": 5,
            "platform": "sub2api",
            "login_username": "admin@example.com",
            "login_password": "password",
        }
        with patch.object(app, "db_query_one", return_value=site):
            result, error, status = app.get_admin_site_or_404(5)
        self.assertEqual(result, site)
        self.assertIsNone(error)
        self.assertEqual(status, 200)

    def test_sub2api_create_route_returns_405_before_reading_body(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/admin/sites/5/channels"
        site = {"id": 5, "platform": "sub2api"}
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "get_admin_site_or_404", return_value=(site, None, 200)
        ), patch.object(app, "read_json_body") as read_body, patch.object(
            app, "create_newapi_channel"
        ) as create, patch.object(app, "json_response") as response:
            app.Handler.do_POST(handler)
        read_body.assert_not_called()
        create.assert_not_called()
        self.assertEqual(response.call_args.args[2], 405)

    def test_connection_test_route_separates_local_and_sub2api_upstream_errors(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/admin/sites/test"
        body = {"platform": "sub2api"}
        cases = [
            (
                (False, {"error_source": "local"}, "主站 Base URL 不能为空"),
                400,
                None,
            ),
            (
                (
                    False,
                    {
                        "error_source": "upstream",
                        "details": {
                            "status": 503,
                            "raw": (
                                '{"code":"MAINTENANCE","message":'
                                '"try later; token=top-secret"}'
                            ),
                        },
                    },
                    "HTTP 503",
                ),
                502,
                "upstream_server",
            ),
        ]

        for result, status, category in cases:
            with self.subTest(status=status):
                with patch.object(
                    app.Handler, "_auth_guard", return_value=False
                ), patch.object(
                    app, "read_json_body", return_value=body
                ), patch.object(
                    app, "test_admin_site_connection", return_value=result
                ), patch.object(app, "json_response") as response:
                    app.Handler.do_POST(handler)

                payload = response.call_args.args[1]
                self.assertEqual(response.call_args.args[2], status)
                self.assertNotIn("top-secret", str(payload))
                self.assertNotIn("raw", str(payload))
                if category:
                    self.assertEqual(payload["category"], category)
                else:
                    self.assertNotIn("category", payload)

    def test_admin_site_write_routes_classify_sub2api_login_failures(self):
        upstream = {
            "status": 401,
            "raw": (
                '{"code":"INVALID_TOKEN","message":'
                '"invalid token; refresh_token=top-secret"}'
            ),
        }
        error = app.Sub2ApiUpstreamError("HTTP 401", upstream)

        create_handler = object.__new__(app.Handler)
        create_handler.path = "/api/admin/sites"
        with patch.object(
            app.Handler, "_auth_guard", return_value=False
        ), patch.object(
            app, "read_json_body", return_value={"platform": "sub2api"}
        ), patch.object(
            app, "create_admin_site", return_value=(False, None, error)
        ), patch.object(app, "json_response") as create_response:
            app.Handler.do_POST(create_handler)

        create_body = create_response.call_args.args[1]
        self.assertEqual(create_response.call_args.args[2], 502)
        self.assertEqual(create_body["category"], "auth")
        self.assertEqual(create_body["upstream_status"], 401)
        self.assertNotIn("top-secret", str(create_body))
        self.assertNotIn("raw", str(create_body))

        update_handler = object.__new__(app.Handler)
        update_handler.path = "/api/admin/sites/5"
        with patch.object(
            app.Handler, "_auth_guard", return_value=False
        ), patch.object(
            app, "read_json_body", return_value={"login_password": "changed"}
        ), patch.object(
            app, "update_admin_site", return_value=(False, error)
        ), patch.object(app, "json_response") as update_response:
            app.Handler.do_PUT(update_handler)

        update_body = update_response.call_args.args[1]
        self.assertEqual(update_response.call_args.args[2], 502)
        self.assertEqual(update_body["category"], "auth")
        self.assertEqual(update_body["upstream_status"], 401)
        self.assertNotIn("top-secret", str(update_body))
        self.assertNotIn("raw", str(update_body))

    def test_sub2api_delete_route_returns_405_without_upstream_call(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/admin/sites/5/channels/7"
        site = {"id": 5, "platform": "sub2api"}
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "get_admin_site_or_404", return_value=(site, None, 200)
        ), patch.object(app, "delete_newapi_channel") as delete, patch.object(
            app, "json_response"
        ) as response:
            app.Handler.do_DELETE(handler)
        delete.assert_not_called()
        self.assertEqual(response.call_args.args[2], 405)

    def test_sub2api_update_route_returns_400_for_local_validation_error(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/admin/sites/5/channels/7"
        site = {"id": 5, "platform": "sub2api", "base_url": "https://sub.example"}
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "get_admin_site_or_404", return_value=(site, None, 200)
        ), patch.object(
            app, "read_json_body", return_value={"password": "must-not-pass"}
        ), patch.object(app, "sub2api_admin_request") as upstream, patch.object(
            app, "json_response"
        ) as response:
            app.Handler.do_PUT(handler)
        upstream.assert_not_called()
        self.assertFalse(response.call_args.args[1]["success"])
        self.assertEqual(response.call_args.args[2], 400)

    def test_sub2api_get_routes_return_sanitized_upstream_error_envelopes(self):
        site = {"id": 5, "platform": "sub2api", "base_url": "https://sub.example"}
        cases = [
            (
                "/api/admin/sites/5/groups",
                "fetch_admin_site_groups",
                (
                    False,
                    {"status": 404, "raw": '{"message":"channel not found"}'},
                    "HTTP 404",
                ),
                404,
                "not_found",
            ),
            (
                "/api/admin/sites/5/channels",
                "fetch_admin_site_channels",
                (
                    False,
                    [],
                    {"status": 429, "raw": '{"message":"too many requests"}'},
                    "HTTP 429",
                ),
                429,
                "rate_limited",
            ),
            (
                "/api/admin/sites/5/channels/7",
                "fetch_admin_site_channel_detail",
                (
                    False,
                    {"status": 503, "raw": '{"message":"maintenance"}'},
                    "HTTP 503",
                ),
                502,
                "upstream_server",
            ),
        ]

        for path, adapter_name, adapter_result, status, category in cases:
            with self.subTest(path=path):
                handler = object.__new__(app.Handler)
                handler.path = path
                with patch.object(
                    app.Handler, "_auth_guard", return_value=False
                ), patch.object(
                    app, "get_admin_site_or_404", return_value=(site, None, 200)
                ), patch.object(
                    app, adapter_name, return_value=adapter_result
                ), patch.object(app, "json_response") as response:
                    app.Handler.do_GET(handler)

                body = response.call_args.args[1]
                self.assertEqual(response.call_args.args[2], status)
                self.assertEqual(body["category"], category)
                self.assertNotIn("raw", str(body))
                self.assertNotIn("upstream", body)

    def test_sub2api_update_route_returns_sanitized_upstream_validation_error(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/admin/sites/5/channels/7"
        site = {"id": 5, "platform": "sub2api", "base_url": "https://sub.example"}
        upstream_payload = {
            "status": 400,
            "raw": (
                '{"code":"INVALID_REQUEST","message":'
                '"max_tokens invalid; token=top-secret"}'
            ),
        }
        with patch.object(
            app.Handler, "_auth_guard", return_value=False
        ), patch.object(
            app, "get_admin_site_or_404", return_value=(site, None, 200)
        ), patch.object(
            app, "read_json_body", return_value={"model_pricing": []}
        ), patch.object(
            app,
            "update_admin_site_channel",
            return_value=(False, upstream_payload, "HTTP 400"),
        ), patch.object(app, "json_response") as response:
            app.Handler.do_PUT(handler)

        body = response.call_args.args[1]
        self.assertEqual(response.call_args.args[2], 400)
        self.assertEqual(body["category"], "validation")
        self.assertEqual(body["upstream_status"], 400)
        self.assertEqual(body["upstream_code"], "INVALID_REQUEST")
        self.assertNotIn("top-secret", str(body))
        self.assertNotIn("raw", str(body))

    def test_sub2api_update_dispatch_uses_allowlisted_adapter(self):
        site = {"id": 5, "platform": "sub2api"}
        with patch.object(
            app,
            "update_sub2api_admin_channel",
            return_value=(True, {"success": True}, None),
        ) as sub, patch.object(app, "update_newapi_channel") as newapi:
            ok, payload, error = app.update_admin_site_channel(
                site, 7, {"status": "disabled"}
            )
        self.assertTrue(ok)
        self.assertIsNone(error)
        sub.assert_called_once_with(site, 7, {"status": "disabled"})
        newapi.assert_not_called()


if __name__ == "__main__":
    unittest.main()
