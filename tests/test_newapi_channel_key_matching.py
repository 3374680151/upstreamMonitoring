import json
import unittest
from unittest.mock import patch

import app


class NewApiChannelKeyMatchingTests(unittest.TestCase):
    def setUp(self):
        with app.NEWAPI_USER_TOKEN_LIST_LOCK:
            app.NEWAPI_USER_TOKEN_LIST_CACHE.clear()
        with app.MAIN_CHANNEL_KEY_REQUEST_LOCK:
            app.MAIN_CHANNEL_KEY_LAST_REQUEST_AT.clear()
            app.MAIN_CHANNEL_KEY_RATE_LIMIT_UNTIL.clear()
            app.MAIN_CHANNEL_KEY_CACHE.clear()

    def test_reuses_matching_monitor_site_login_when_channel_binding_is_empty(self):
        main_site = {"id": 2}
        monitor_site = {
            "id": 4,
            "base_url": "https://api.example",
            "platform": "newapi",
            "access_token": "upstream-token",
            "access_user_id": "115",
        }
        with patch.object(app, "get_channel_upstream_binding", return_value=None), \
             patch.object(
                 app,
                 "fetch_newapi_channel_detail",
                 return_value=(True, {"data": {"base_url": "https://api.example"}}, None),
             ), \
             patch.object(app, "find_monitor_site_for_channel", return_value=monitor_site), \
             patch.object(app, "fetch_newapi_channel_key", return_value=(True, "sk-main", None)), \
             patch.object(
                 app,
                 "find_newapi_user_token_by_key",
                 return_value=({"group": "default"}, None),
             ) as find_key, \
             patch.object(
                 app,
                 "fetch_newapi_groups_with_access_token",
                 return_value=(True, {"data": {"default": {"ratio": 0.1}}}, None),
             ), \
             patch.object(app, "persist_channel_match"):
            ok, payload, error = app.match_channel_upstream_binding(main_site, 21)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertTrue(payload["configured"])
        self.assertTrue(payload["inherited_from_monitor"])
        self.assertEqual(payload["matched_groups"][0]["ratio"], 0.1)
        find_key.assert_called_once_with(monitor_site, "sk-main")

    def test_public_only_newapi_monitor_prompts_configuration_before_reading_key(self):
        main_site = {"id": 2}
        public_monitor = {
            "id": 4,
            "base_url": "https://api.example",
            "platform": "newapi",
            "access_token": "",
            "access_user_id": "",
        }
        with patch.object(app, "get_channel_upstream_binding", return_value=None), \
             patch.object(
                 app,
                 "fetch_newapi_channel_detail",
                 return_value=(True, {"data": {"base_url": "https://api.example"}}, None),
             ), \
             patch.object(app, "find_monitor_site_for_channel", return_value=public_monitor), \
             patch.object(app, "fetch_newapi_channel_key") as fetch_key:
            ok, payload, error = app.match_channel_upstream_binding(main_site, 21)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertFalse(payload["configured"])
        self.assertIn("优先配置渠道", payload["match_message"])
        fetch_key.assert_not_called()

    def test_matches_newapi_user_token_by_mask_then_confirms_full_key(self):
        site = {
            "base_url": "https://newapi-user.example",
            "access_token": "dashboard-token",
            "access_user_id": "115",
        }
        target = "sk-abcd12345678wxyz"
        tokens = [{"id": 7, "key": "abcd**********wxyz", "group": "plus-team"}]
        with patch.object(
            app, "fetch_all_newapi_user_tokens", return_value=(True, tokens, None)
        ) as fetch_tokens, patch.object(
            app, "fetch_newapi_user_token_key", return_value=(True, "abcd12345678wxyz", None)
        ) as fetch_key:
            matched, error = app.find_newapi_user_token_by_key(site, target)

        self.assertIsNone(error)
        self.assertEqual(matched["group"], "plus-team")
        fetch_tokens.assert_called_once_with(site)
        fetch_key.assert_called_once_with(site, 7)

    def test_newapi_user_token_match_does_not_use_admin_channel_api(self):
        main_site = {"id": 2}
        monitor_site = {
            "id": 4,
            "base_url": "https://api.example",
            "platform": "newapi",
            "access_token": "user-dashboard-token",
            "access_user_id": "115",
        }
        with patch.object(app, "get_channel_upstream_binding", return_value=None), \
             patch.object(
                 app,
                 "fetch_newapi_channel_detail",
                 return_value=(True, {"data": {"base_url": "https://api.example"}}, None),
             ), \
             patch.object(app, "find_monitor_site_for_channel", return_value=monitor_site), \
             patch.object(app, "fetch_newapi_channel_key", return_value=(True, "sk-main", None)), \
             patch.object(
                 app,
                 "find_newapi_user_token_by_key",
                 return_value=({"group": "gpt-free"}, None),
             ) as find_token, \
             patch.object(app, "fetch_all_newapi_channels") as fetch_admin_channels, \
             patch.object(
                 app,
                 "fetch_newapi_groups_with_access_token",
                 return_value=(True, {"data": {"gpt-free": {"ratio": 0.01}}}, None),
             ), \
             patch.object(app, "persist_channel_match"):
            ok, payload, error = app.match_channel_upstream_binding(main_site, 21)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["matched_groups"][0]["name"], "gpt-free")
        self.assertEqual(payload["matched_groups"][0]["ratio"], 0.01)
        find_token.assert_called_once_with(monitor_site, "sk-main")
        fetch_admin_channels.assert_not_called()

    def test_newapi_user_token_pagination_starts_at_one_and_reads_all_pages(self):
        site = {
            "base_url": "https://newapi-pages.example",
            "access_token": "dashboard-token",
            "access_user_id": "115",
        }
        responses = [
            (
                True,
                {
                    "success": True,
                    "data": {
                        "items": [{"id": 1}, {"id": 2}],
                        "total": 3,
                        "page": 1,
                        "page_size": 2,
                    },
                },
                None,
            ),
            (
                True,
                {
                    "success": True,
                    "data": {
                        "items": [{"id": 3}],
                        "total": 3,
                        "page": 2,
                        "page_size": 2,
                    },
                },
                None,
            ),
        ]
        with patch.object(app, "request_json", side_effect=responses) as request:
            ok, tokens, error = app.fetch_all_newapi_user_tokens(site, page_size=2)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual([item["id"] for item in tokens], [1, 2, 3])
        urls = [call.args[0] for call in request.call_args_list]
        self.assertIn("p=1", urls[0])
        self.assertIn("p=2", urls[1])
        self.assertNotIn("p=0", " ".join(urls))

    def test_reports_rate_limit_without_resetting_security_proof(self):
        site = {"id": 2, "base_url": "https://newapi-rate-limited.example"}
        with patch.object(
            app,
            "request_json",
            return_value=(False, {"status": 429, "raw": ""}, "HTTP 429"),
        ):
            ok, key, message = app.fetch_newapi_channel_key(site, 21)

        self.assertFalse(ok)
        self.assertEqual(key, "")
        self.assertIn("触发限流", message)

    def test_skips_repeated_key_request_during_rate_limit_cooldown(self):
        site = {"id": 2, "base_url": "https://newapi-rate-limited.example"}
        with patch.object(
            app,
            "request_json",
            return_value=(False, {"status": 429, "raw": ""}, "HTTP 429"),
        ) as request:
            first = app.fetch_newapi_channel_key(site, 21)
            second = app.fetch_newapi_channel_key(site, 22)

        self.assertIn("触发限流", first[2])
        self.assertIn("暂停请求", second[2])
        request.assert_called_once()

    def test_preserves_last_successful_groups_when_refresh_fails(self):
        binding = {
            "matched_groups_json": json.dumps([{"name": "pro", "ratio": 0.05}]),
        }
        with patch.object(app, "get_channel_upstream_binding", return_value=binding), \
             patch.object(app, "db_execute") as save:
            app.persist_channel_match(2, 10, "error", "临时限流", [])

        self.assertIn("match_status = ?", save.call_args.args[0])
        self.assertNotIn("matched_groups_json = ?", save.call_args.args[0])

    def test_clears_last_successful_groups_for_definitive_key_miss(self):
        binding = {
            "matched_groups_json": json.dumps([{"name": "pro", "ratio": 0.05}]),
        }
        with patch.object(app, "get_channel_upstream_binding", return_value=binding), \
             patch.object(app, "db_execute") as save:
            groups = app.persist_channel_match(
                2,
                10,
                "key_not_found",
                "当前 key 未在上游列表中找到",
                [],
            )

        self.assertEqual(groups, [])
        self.assertIn("matched_groups_json = ?", save.call_args.args[0])
        self.assertEqual(json.loads(save.call_args.args[1][2]), [])

    def test_partial_match_does_not_overwrite_a_known_ratio_with_null(self):
        binding = {
            "matched_groups_json": json.dumps(
                [{"name": "pro", "ratio": 0.05, "desc": "上次成功"}]
            ),
        }
        partial = [
            {
                "name": "pro",
                "ratio": None,
                "ratio_type": "text",
                "desc": "",
                "available_to_login": False,
            }
        ]
        with patch.object(app, "get_channel_upstream_binding", return_value=binding), \
             patch.object(app, "db_execute") as save:
            groups = app.persist_channel_match(
                2,
                10,
                "matched_partial",
                "分组数据不完整",
                partial,
            )

        self.assertEqual(groups[0]["ratio"], 0.05)
        saved_groups = json.loads(save.call_args.args[1][2])
        self.assertEqual(saved_groups[0]["ratio"], 0.05)
        self.assertFalse(saved_groups[0]["available_to_login"])

    def test_creates_result_cache_for_inherited_monitor_match(self):
        inserted = {"matched_groups_json": None}
        groups = [{"name": "pro", "ratio": 0.05}]
        with patch.object(
            app,
            "get_channel_upstream_binding",
            side_effect=[None, inserted],
        ), patch.object(app, "db_execute") as execute:
            app.persist_channel_match(2, 22, "matched", "匹配成功", groups)

        self.assertEqual(execute.call_count, 2)
        self.assertIn(
            "INSERT INTO channel_upstream_bindings",
            execute.call_args_list[0].args[0],
        )
        self.assertIn("matched_groups_json = ?", execute.call_args_list[1].args[0])

    def test_admin_browser_login_persists_session_cookie_and_access_token(self):
        site = {
            "id": 2,
            "base_url": "https://main.example",
            "login_username": "admin@example.com",
            "login_password": "password",
        }
        login_response = (
            True,
            {
                "success": True,
                "data": {
                    "access_token": "dashboard-token",
                    "access_expires_at": 4102444800,
                    "session": {"sid": "session-1"},
                },
            },
            None,
            {"set-cookie": ["new_api_refresh=refresh-token; Path=/api/user/auth; HttpOnly"]},
        )
        with patch.object(app, "request_json_with_headers", return_value=login_response) as request, \
             patch.object(app, "db_execute") as save:
            ok, error = app.ensure_admin_site_browser_session(site)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(site["browser_access_token"], "dashboard-token")
        self.assertEqual(site["browser_session_id"], "session-1")
        self.assertEqual(site["browser_refresh_cookie"], "new_api_refresh=refresh-token")
        self.assertEqual(request.call_args.args[0], "https://main.example/api/user/login")
        save.assert_called_once()

    def test_reuses_browser_session_when_expiry_is_not_returned(self):
        site = {
            "id": 2,
            "base_url": "https://main.example",
            "browser_access_token": "dashboard-token",
            "browser_session_id": "session-1",
            "browser_refresh_cookie": "",
            "browser_access_expires_at": 0,
        }
        with patch.object(app, "request_json_with_headers") as request, \
             patch.object(app, "db_execute") as save:
            ok, error = app.ensure_admin_site_browser_session(site)

        self.assertTrue(ok)
        self.assertIsNone(error)
        request.assert_not_called()
        save.assert_not_called()

    def test_does_not_refresh_browser_access_token_before_refresh_window(self):
        site = {
            "id": 2,
            "base_url": "https://main.example",
            "browser_access_token": "dashboard-token",
            "browser_session_id": "session-1",
            "browser_refresh_cookie": "new_api_refresh=refresh-token",
            "browser_access_expires_at": 4102444800,
        }
        with patch.object(
            app,
            "request_json_with_headers",
            return_value=(False, {}, "unexpected refresh", {}),
        ) as request:
            ok, error = app.ensure_admin_site_browser_session(site)

        self.assertTrue(ok)
        self.assertIsNone(error)
        request.assert_not_called()

    def test_refreshes_near_expiry_with_same_origin_and_persists_rotation(self):
        site = {
            "id": 2,
            "base_url": "https://main.example/console",
            "browser_access_token": "old-dashboard-token",
            "browser_session_id": "session-1",
            "browser_refresh_cookie": "new_api_refresh=old-refresh-token",
            "browser_access_expires_at": 1,
        }
        refresh_response = (
            True,
            {
                "success": True,
                "data": {
                    "access_token": "new-dashboard-token",
                    "access_expires_at": 4102444800,
                    "session": {"sid": "session-1"},
                },
            },
            None,
            {
                "set-cookie": [
                    "new_api_refresh=new-refresh-token; Path=/api/user/auth; HttpOnly; Secure"
                ]
            },
        )
        with patch.object(app, "db_query_one", return_value=None), \
             patch.object(app, "request_json_with_headers", return_value=refresh_response) as request, \
             patch.object(app, "db_execute"):
            ok, error = app.ensure_admin_site_browser_session(site)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(request.call_args.kwargs["headers"]["Origin"], "https://main.example")
        self.assertEqual(site["browser_access_token"], "new-dashboard-token")
        self.assertEqual(site["browser_refresh_cookie"], "new_api_refresh=new-refresh-token")

    def test_admin_site_origin_rejects_malformed_ipv6_without_raising(self):
        try:
            origin = app._admin_site_origin("https://[broken")
        except ValueError as exc:
            self.fail(f"malformed URL raised ValueError: {exc}")

        self.assertEqual(origin, "")

    def test_admin_site_origin_rejects_url_without_hostname(self):
        self.assertEqual(app._admin_site_origin("https://:443"), "")

    def test_reports_origin_guard_failure_without_exposing_refresh_cookie(self):
        site = {
            "id": 2,
            "base_url": "https://main.example",
            "browser_access_token": "old-dashboard-token",
            "browser_session_id": "session-1",
            "browser_refresh_cookie": "new_api_refresh=secret-refresh-token",
            "browser_access_expires_at": 1,
        }
        refresh_response = (
            False,
            {
                "status": 403,
                "raw": json.dumps(
                    {
                        "code": "AUTH_ORIGIN_FORBIDDEN",
                        "message": "request origin is not allowed",
                    }
                ),
            },
            "HTTP 403",
            {"set-cookie": []},
        )
        with patch.object(app, "db_query_one", return_value=None), \
             patch.object(app, "request_json_with_headers", return_value=refresh_response), \
             patch.object(app, "db_execute"):
            ok, error = app.ensure_admin_site_browser_session(site)

        self.assertFalse(ok)
        self.assertIn("Origin", error)
        self.assertNotIn("secret-refresh-token", error)

    def test_refresh_failure_persists_sanitized_admin_site_error(self):
        site = {
            "id": 2,
            "base_url": "https://main.example",
            "browser_access_token": "old-dashboard-token",
            "browser_session_id": "session-1",
            "browser_refresh_cookie": "new_api_refresh=secret-refresh-token",
            "browser_access_expires_at": 0,
        }
        refresh_response = (
            False,
            {
                "status": 403,
                "raw": json.dumps(
                    {
                        "code": "AUTH_ORIGIN_FORBIDDEN",
                        "message": "request origin is not allowed",
                    }
                ),
            },
            "HTTP 403",
            {"set-cookie": []},
        )
        with patch.object(app, "db_query_one", return_value=None), \
             patch.object(app, "request_json_with_headers", return_value=refresh_response), \
             patch.object(app, "_persist_admin_browser_login_error") as persist:
            ok, error = app.refresh_admin_site_browser_session(site, force=True)

        self.assertFalse(ok)
        self.assertIn("Origin", error)
        self.assertNotIn("secret-refresh-token", error)
        persist.assert_called_once_with(site, error)

    def test_reports_revoked_refresh_session_as_requiring_login_and_2fa(self):
        site = {
            "id": 2,
            "base_url": "https://main.example",
            "browser_access_token": "old-dashboard-token",
            "browser_session_id": "session-1",
            "browser_refresh_cookie": "new_api_refresh=secret-refresh-token",
            "browser_access_expires_at": 1,
        }
        refresh_response = (
            False,
            {
                "status": 401,
                "raw": json.dumps(
                    {"code": "AUTH_SESSION_REVOKED", "message": "Unauthorized"}
                ),
            },
            "HTTP 401",
            {"set-cookie": []},
        )
        with patch.object(app, "db_query_one", return_value=None), \
             patch.object(app, "request_json_with_headers", return_value=refresh_response), \
             patch.object(app, "db_execute"):
            ok, error = app.ensure_admin_site_browser_session(site)

        self.assertFalse(ok)
        self.assertIn("Session 已失效", error)
        self.assertIn("2FA", error)
        self.assertNotIn("secret-refresh-token", error)

    def test_force_refresh_reuses_session_rotated_by_another_caller(self):
        site = {
            "id": 2,
            "base_url": "https://main.example",
            "browser_access_token": "old-dashboard-token",
            "browser_session_id": "session-1",
            "browser_refresh_cookie": "new_api_refresh=old-refresh-token",
            "browser_access_expires_at": 1,
        }
        latest = {
            "browser_access_token": "new-dashboard-token",
            "browser_session_id": "session-1",
            "browser_refresh_cookie": "new_api_refresh=new-refresh-token",
            "browser_access_expires_at": 4102444800,
        }
        with patch.object(app, "db_query_one", return_value=latest), \
             patch.object(
                 app,
                 "request_json_with_headers",
                 return_value=(False, {}, "unexpected refresh", {}),
             ) as request:
            ok, error = app.refresh_admin_site_browser_session(site, force=True)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(site["browser_access_token"], "new-dashboard-token")
        request.assert_not_called()

    def test_force_refresh_reuses_rotated_session_when_expiry_is_unknown(self):
        site = {
            "id": 2,
            "base_url": "https://main.example",
            "browser_access_token": "old-dashboard-token",
            "browser_session_id": "session-1",
            "browser_refresh_cookie": "new_api_refresh=old-refresh-token",
            "browser_access_expires_at": 0,
        }
        latest = {
            "browser_access_token": "new-dashboard-token",
            "browser_session_id": "session-1",
            "browser_refresh_cookie": "new_api_refresh=new-refresh-token",
            "browser_access_expires_at": 0,
        }
        with patch.object(app, "db_query_one", return_value=latest), \
             patch.object(
                 app,
                 "request_json_with_headers",
                 return_value=(False, {}, "unexpected refresh", {}),
             ) as request:
            ok, error = app.refresh_admin_site_browser_session(site, force=True)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(site["browser_access_token"], "new-dashboard-token")
        request.assert_not_called()

    def test_caches_successful_channel_key_until_forced_refresh(self):
        site = {"id": 2, "base_url": "https://main.example"}
        responses = [
            (True, {"success": True, "data": {"key": "sk-first"}}, None),
            (True, {"success": True, "data": {"key": "sk-second"}}, None),
        ]
        with patch.object(app, "request_json", side_effect=responses) as request:
            first = app.fetch_newapi_channel_key(site, 10)
            cached = app.fetch_newapi_channel_key(site, 10)
            forced = app.fetch_newapi_channel_key(site, 10, force_refresh=True)

        self.assertEqual(first[1], "sk-first")
        self.assertEqual(cached[1], "sk-first")
        self.assertEqual(forced[1], "sk-second")
        self.assertEqual(request.call_count, 2)

    def test_retries_channel_key_once_after_access_token_expiry(self):
        site = {
            "id": 2,
            "base_url": "https://main.example",
            "browser_access_token": "old-dashboard-token",
            "browser_session_id": "session-1",
            "browser_refresh_cookie": "new_api_refresh=refresh-token",
            "browser_access_expires_at": 4102444800,
        }
        responses = [
            (
                False,
                {
                    "status": 401,
                    "raw": json.dumps({"code": "AUTH_TOKEN_EXPIRED"}),
                },
                "HTTP 401",
            ),
            (True, {"success": True, "data": {"key": "sk-refreshed"}}, None),
        ]
        with patch.object(app, "get_cached_admin_channel_key", return_value=""), \
             patch.object(app, "ensure_admin_site_browser_session", return_value=(True, None)), \
             patch.object(app, "refresh_admin_site_browser_session", return_value=(True, None)) as refresh, \
             patch.object(app, "persist_admin_channel_key"), \
             patch.object(app, "request_json", side_effect=responses) as request:
            ok, key, error = app.fetch_newapi_channel_key(site, 10)

        self.assertTrue(ok)
        self.assertEqual(key, "sk-refreshed")
        self.assertIsNone(error)
        self.assertEqual(request.call_count, 2)
        refresh.assert_called_once_with(site, force=True)

    def test_stops_after_one_channel_key_retry(self):
        site = {
            "id": 2,
            "base_url": "https://main.example",
            "browser_access_token": "old-dashboard-token",
            "browser_session_id": "session-1",
            "browser_refresh_cookie": "new_api_refresh=refresh-token",
            "browser_access_expires_at": 4102444800,
        }
        expired = (
            False,
            {
                "status": 401,
                "raw": json.dumps({"code": "AUTH_TOKEN_EXPIRED"}),
            },
            "HTTP 401",
        )
        with patch.object(app, "get_cached_admin_channel_key", return_value=""), \
             patch.object(app, "ensure_admin_site_browser_session", return_value=(True, None)), \
             patch.object(app, "refresh_admin_site_browser_session", return_value=(True, None)) as refresh, \
             patch.object(app, "request_json", side_effect=[expired, expired]) as request:
            ok, key, error = app.fetch_newapi_channel_key(site, 10)

        self.assertFalse(ok)
        self.assertEqual(key, "")
        self.assertIn("HTTP 401", error)
        self.assertEqual(request.call_count, 2)
        refresh.assert_called_once_with(site, force=True)

    def test_uses_persisted_admin_key_without_requesting_2fa_protected_endpoint(self):
        site = {
            "id": 2,
            "base_url": "https://main.example",
            "security_proof": "expired-proof",
            "browser_access_token": "dashboard-token",
            "browser_session_id": "session-1",
        }
        with patch.object(app, "get_cached_admin_channel_key", return_value="sk-persisted"), \
             patch.object(app, "request_json") as request:
            ok, key, error = app.fetch_newapi_channel_key(site, 10)

        self.assertTrue(ok)
        self.assertEqual(key, "sk-persisted")
        self.assertIsNone(error)
        request.assert_not_called()

    def test_match_reuses_saved_binding_key_before_protected_main_site_endpoint(self):
        main_site = {"id": 2}
        binding = {
            "upstream_base_url": "https://api.example",
            "upstream_platform": "newapi",
            "auth_mode": "token",
            "access_token": "user-token",
            "access_user_id": "115",
            "channel_key": "sk-saved-binding",
        }
        with patch.object(app, "get_channel_upstream_binding", return_value=binding), \
             patch.object(app, "get_cached_admin_channel_key", return_value=""), \
             patch.object(
                 app,
                 "fetch_newapi_channel_detail",
                 return_value=(True, {"data": {"base_url": "https://api.example", "key": ""}}, None),
             ), \
             patch.object(app, "find_monitor_site_for_channel", return_value=None), \
             patch.object(
                 app,
                 "fetch_newapi_channel_key",
                 return_value=(False, "", "protected endpoint must not be called"),
             ) as fetch_key, \
             patch.object(app, "persist_admin_channel_key") as persist_key, \
             patch.object(
                 app,
                 "find_newapi_user_token_by_key",
                 return_value=({"group": "default"}, None),
             ), \
             patch.object(
                 app,
                 "fetch_newapi_groups_with_access_token",
                 return_value=(True, {"data": {"default": {"ratio": 0.1}}}, None),
             ), \
             patch.object(app, "persist_channel_match"):
            ok, payload, error = app.match_channel_upstream_binding(main_site, 22)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["match_status"], "matched")
        fetch_key.assert_not_called()
        persist_key.assert_called_once_with(2, 22, "sk-saved-binding")

    def test_missing_uncached_key_returns_actionable_verification_state(self):
        main_site = {"id": 2}
        binding = {
            "upstream_base_url": "https://api.example",
            "upstream_platform": "newapi",
            "auth_mode": "token",
            "access_token": "user-token",
            "access_user_id": "115",
            "channel_key": "",
            "matched_at": "2026-07-28T12:00:00Z",
        }
        stale_groups = [{"name": "pro", "ratio": 0.05}]
        for key_error in (
            "主站尚未完成 key 读取安全验证",
            "主站网页登录 Session 已失效，请重新完成主站网页登录和 2FA",
        ):
            with self.subTest(key_error=key_error), \
                 patch.object(app, "get_channel_upstream_binding", return_value=binding), \
                 patch.object(app, "get_cached_admin_channel_key", return_value=""), \
                 patch.object(
                     app,
                     "fetch_newapi_channel_detail",
                     return_value=(True, {"data": {"base_url": "https://api.example", "key": ""}}, None),
                 ), \
                 patch.object(app, "find_monitor_site_for_channel", return_value=None), \
                 patch.object(
                     app,
                     "fetch_newapi_channel_key",
                     return_value=(False, "", key_error),
                 ), \
                 patch.object(
                     app,
                     "persist_channel_match",
                     return_value=stale_groups,
                 ) as persist_match:
                ok, payload, error = app.match_channel_upstream_binding(main_site, 22)

            self.assertTrue(ok)
            self.assertIsNone(error)
            self.assertEqual(payload["match_status"], "needs_key_verification")
            self.assertIn("渠道运行正常", payload["match_message"])
            self.assertIn("编辑主站", payload["match_message"])
            self.assertIn("2FA", payload["match_message"])
            self.assertNotIn("编辑渠道", payload["match_message"])
            self.assertIn("优先复用", payload["match_message"])
            self.assertNotIn("不再重复验证", payload["match_message"])
            self.assertEqual(payload["matched_groups"], stale_groups)
            self.assertEqual(payload["matched_at"], "2026-07-28T12:00:00Z")
            persist_match.assert_called_once_with(
                2,
                22,
                "needs_key_verification",
                payload["match_message"],
                [],
            )

    def test_create_channel_persists_submitted_plaintext_key(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/admin/sites/2/channels"
        body = {"name": "created", "key": "sk-created"}
        with patch.object(app.Handler, "_auth_guard", return_value=False), \
             patch.object(app, "read_json_body", return_value=body), \
             patch.object(app, "get_admin_site_or_404", return_value=({"id": 2}, None, 200)), \
             patch.object(
                 app,
                 "create_newapi_channel",
                 return_value=(True, {"success": True, "data": {"id": 22}}, None),
             ), \
             patch.object(app, "fetch_all_newapi_channels", return_value=(True, [], None)), \
             patch.object(app, "persist_admin_channel_key") as persist_key, \
             patch.object(app, "json_response"):
            app.Handler.do_POST(handler)

        persist_key.assert_called_once_with(2, 22, "sk-created")

    def test_create_channel_resolves_missing_upstream_id_before_caching_key(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/admin/sites/2/channels"
        body = {
            "name": "created",
            "base_url": "https://provider.example",
            "key": "sk-created",
        }
        before = [
            {"id": 21, "name": "existing", "base_url": "https://old.example"},
        ]
        after = before + [
            {"id": 22, "name": "created", "base_url": "https://provider.example"},
        ]
        with patch.object(app.Handler, "_auth_guard", return_value=False), \
             patch.object(app, "read_json_body", return_value=body), \
             patch.object(
                 app,
                 "get_admin_site_or_404",
                 return_value=({"id": 2, "base_url": "https://main.example"}, None, 200),
             ), \
             patch.object(
                 app,
                 "create_newapi_channel",
                 return_value=(True, {"success": True}, None),
             ), \
             patch.object(app, "fetch_all_newapi_channels", side_effect=[(True, before, None), (True, after, None)]), \
             patch.object(app, "persist_admin_channel_key") as persist_key, \
             patch.object(app, "json_response") as response:
            app.Handler.do_POST(handler)

        persist_key.assert_called_once_with(2, 22, "sk-created")
        self.assertEqual(response.call_args.args[1]["id"], 22)

    def test_update_channel_replaces_cached_key_with_submitted_plaintext_key(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/admin/sites/2/channels/22"
        body = {"key": "sk-updated"}
        with patch.object(app.Handler, "_auth_guard", return_value=False), \
             patch.object(app, "read_json_body", return_value=body), \
             patch.object(app, "get_admin_site_or_404", return_value=({"id": 2}, None, 200)), \
             patch.object(
                 app,
                 "update_newapi_channel",
                 return_value=(True, {"success": True}, None),
             ), \
             patch.object(app, "persist_admin_channel_key") as persist_key, \
             patch.object(app, "clear_admin_channel_key") as clear_key, \
             patch.object(app, "json_response"):
            app.Handler.do_PUT(handler)

        persist_key.assert_called_once_with(2, 22, "sk-updated")
        clear_key.assert_not_called()

    def test_saving_unchanged_login_fields_does_not_clear_verified_session(self):
        existing = {
            "id": 2,
            "name": "main",
            "base_url": "https://main.example",
            "login_username": "admin",
            "login_password": "password",
        }
        with patch.object(app, "db_query_one", return_value=existing), \
             patch.object(app, "db_execute") as execute:
            ok, error = app.update_admin_site(
                2,
                {
                    "name": "main",
                    "base_url": "https://main.example",
                    "access_user_id": "",
                    "access_token": "",
                    "login_username": "admin",
                    "login_password": "",
                },
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        update_sql = execute.call_args.args[0]
        self.assertNotIn("browser_access_token = NULL", update_sql)
        self.assertNotIn("security_proof = NULL", update_sql)

    def test_channel_key_headers_prefer_dashboard_session(self):
        site = {
            "base_url": "https://main.example",
            "access_token": "admin-token",
            "access_user_id": "1",
            "browser_access_token": "dashboard-token",
            "browser_session_id": "session-1",
            "browser_refresh_cookie": "new_api_refresh=refresh-token",
            "security_proof": "proof-token",
        }

        headers = app.site_newapi_channel_key_headers(site)

        self.assertEqual(headers["Authorization"], "Bearer dashboard-token")
        self.assertEqual(headers["X-Auth-Session"], "session-1")
        self.assertEqual(headers["Cookie"], "new_api_refresh=refresh-token")
        self.assertEqual(headers["X-Security-Proof"], "proof-token")
        self.assertNotEqual(headers["Authorization"], "admin-token")


if __name__ == "__main__":
    unittest.main()
