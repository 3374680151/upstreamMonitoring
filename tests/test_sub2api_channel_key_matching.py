import unittest
import threading
import time
from unittest.mock import patch

import app


class Sub2ApiChannelKeyMatchingTests(unittest.TestCase):
    def setUp(self):
        with app.SUB2API_REFRESH_LOCKS_GUARD:
            app.SUB2API_REFRESH_CACHE.clear()

    def test_password_mode_reuses_single_login_for_groups_and_keys(self):
        main_site = {"id": 2}
        monitor_site = {
            "id": 7,
            "base_url": "https://sub2api.example",
            "platform": "sub2api",
            "auth_mode": "password",
            "login_username": "user@example.com",
            "login_password": "password",
            "access_token": "",
            "refresh_token": "",
        }
        groups_payload = {
            "success": True,
            "data": [
                {
                    "id": 9,
                    "name": "GPT-plus",
                    "rate_multiplier": 0.1,
                    "description": "base group",
                }
            ],
            # 当前用户专属倍率才是该 key 实际使用的倍率。
            "user_rates": {"9": 0.04},
        }
        keys_payload = {
            "success": True,
            "data": {
                "items": [
                    {
                        "id": 3,
                        "key": "sk-current",
                        "group_id": 9,
                        "group": {
                            "id": 9,
                            "name": "GPT-plus",
                            "rate_multiplier": 0.1,
                        },
                    }
                ]
            },
        }

        with patch.object(app, "get_channel_upstream_binding", return_value=None), \
             patch.object(
                 app,
                 "fetch_newapi_channel_detail",
                 return_value=(True, {"data": {"base_url": monitor_site["base_url"]}}, None),
             ), \
             patch.object(app, "find_monitor_site_for_channel", return_value=monitor_site), \
             patch.object(app, "fetch_newapi_channel_key", return_value=(True, "sk-current", None)), \
             patch.object(
                 app,
                 "sub2api_login",
                 return_value=(True, "session-access", {"data": {}}, None),
             ) as login, \
             patch.object(
                 app,
                 "fetch_sub2api_groups_by_token",
                 return_value=(True, groups_payload, None),
             ) as fetch_groups, \
             patch.object(
                 app,
                 "fetch_sub2api_keys_by_token",
                 return_value=(True, keys_payload, None),
             ) as fetch_keys, \
             patch.object(app, "find_newapi_user_token_by_key") as newapi_match, \
             patch.object(app, "persist_channel_match"):
            ok, payload, error = app.match_channel_upstream_binding(main_site, 21)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["upstream_platform"], "sub2api")
        self.assertEqual(payload["matched_groups"][0]["name"], "GPT-plus")
        self.assertEqual(payload["matched_groups"][0]["ratio"], 0.04)
        login.assert_called_once()
        fetch_groups.assert_called_once_with("https://sub2api.example", "session-access")
        fetch_keys.assert_called_once_with("https://sub2api.example", "session-access")
        newapi_match.assert_not_called()

    def test_binding_persists_refresh_from_groups_when_keys_reuse_rotated_access(self):
        main_site = {"id": 2}
        binding = {
            "upstream_base_url": "https://sub2api.example",
            "upstream_platform": "sub2api",
            "auth_mode": "browser",
            "login_username": "user@example.com",
            "login_password": "saved-password",
            "access_token": "expired-access",
            "refresh_token": "old-refresh",
            "channel_key": "sk-current",
        }
        rotated = {
            "access_token": "rotated-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
        }
        groups_payload = {
            "success": True,
            "data": [{"id": 9, "name": "GPT-plus", "rate_multiplier": 0.1}],
            "user_rates": {"9": 0.04},
            "refreshed_auth": rotated,
            "_auth_context": {"access_token": "rotated-access"},
        }
        keys_payload = {
            "success": True,
            "data": {
                "items": [
                    {
                        "key": "sk-current",
                        "group_id": 9,
                        "group": {"id": 9, "name": "GPT-plus"},
                    }
                ]
            },
            "_auth_context": {"access_token": "rotated-access"},
        }

        with patch.object(app, "get_channel_upstream_binding", return_value=binding), \
             patch.object(
                 app,
                 "fetch_newapi_channel_detail",
                 return_value=(True, {"data": {"base_url": binding["upstream_base_url"]}}, None),
             ), \
             patch.object(app, "find_monitor_site_for_channel", return_value=None), \
             patch.object(app, "persist_admin_channel_key"), \
             patch.object(
                 app, "fetch_sub2api_user_groups", return_value=(True, groups_payload, None)
             ), \
             patch.object(
                 app, "fetch_sub2api_keys", return_value=(True, keys_payload, None)
             ) as fetch_keys, \
             patch.object(app, "persist_channel_binding_refreshed_auth") as persist_auth, \
             patch.object(app, "persist_channel_match"):
            ok, payload, error = app.match_channel_upstream_binding(main_site, 21)

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertTrue(payload["has_access_token"])
        self.assertTrue(payload["has_refresh_token"])
        fetch_keys.assert_called_once()
        self.assertEqual(fetch_keys.call_args.kwargs["access_token"], "rotated-access")
        persist_auth.assert_called_once_with(
            2,
            21,
            rotated,
            expected_access_token="expired-access",
            expected_refresh_token="old-refresh",
        )

    def test_sub2api_key_list_reads_every_page(self):
        responses = [
            (
                True,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "items": [{"id": 1, "key": "sk-one"}, {"id": 2, "key": "sk-two"}],
                        "total": 3,
                        "page": 1,
                        "page_size": 2,
                        "pages": 2,
                    },
                },
                None,
            ),
            (
                True,
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "items": [{"id": 3, "key": "sk-three"}],
                        "total": 3,
                        "page": 2,
                        "page_size": 2,
                        "pages": 2,
                    },
                },
                None,
            ),
        ]
        with patch.object(app, "request_json", side_effect=responses) as request:
            ok, payload, error = app.fetch_sub2api_keys_by_token(
                "https://sub2api.example", "access", page_size=2
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(
            [item["key"] for item in payload["data"]["items"]],
            ["sk-one", "sk-two", "sk-three"],
        )
        urls = [call.args[0] for call in request.call_args_list]
        self.assertEqual(
            urls,
            [
                "https://sub2api.example/api/v1/keys?page=1&page_size=2",
                "https://sub2api.example/api/v1/keys?page=2&page_size=2",
            ],
        )

    def test_sub2api_key_list_rejects_silent_truncation_at_max_pages(self):
        responses = [
            (True, {"code": 0, "data": {"items": [{"id": 1, "key": "sk-one"}]}}, None),
            (True, {"code": 0, "data": {"items": [{"id": 2, "key": "sk-two"}]}}, None),
        ]
        with patch.object(app, "request_json", side_effect=responses):
            ok, payload, error = app.fetch_sub2api_keys_by_token(
                "https://sub2api.example", "access", page_size=1, max_pages=2
            )

        self.assertFalse(ok)
        self.assertIn("最大分页页数", error)
        self.assertTrue(payload["truncated"])

    def test_sub2api_refresh_serializes_and_reuses_rotated_result(self):
        calls = []

        def refresh(_base_url, _refresh_token):
            calls.append(True)
            time.sleep(0.05)
            return True, {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            }, None

        results = []

        def worker():
            results.append(
                app.refresh_sub2api_auth(
                    "https://refresh.example", "old-access", "old-refresh"
                )
            )

        with patch.object(app, "sub2api_refresh_token", side_effect=refresh):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item[0] for item in results))
        self.assertEqual(
            {item[1]["refresh_token"] for item in results}, {"new-refresh"}
        )

    def test_token_mode_refreshes_expired_login_and_returns_new_tokens(self):
        expired_payload = {"groups": {"status": 401, "message": "token expired"}}
        fresh_payload = {
            "success": True,
            "data": [{"id": 1, "name": "default", "rate_multiplier": 1}],
            "user_rates": {},
        }
        with patch.object(
            app,
            "fetch_sub2api_groups_by_token",
            side_effect=[(False, expired_payload, "HTTP 401"), (True, fresh_payload, None)],
        ) as fetch_groups, patch.object(
            app,
            "sub2api_refresh_token",
            return_value=(
                True,
                {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600},
                None,
            ),
        ) as refresh:
            ok, payload, error = app.fetch_sub2api_user_groups(
                "https://sub2api.example",
                auth_mode="token",
                access_token="expired-access",
                refresh_token="old-refresh",
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["refreshed_auth"]["access_token"], "new-access")
        self.assertEqual(payload["refreshed_auth"]["refresh_token"], "new-refresh")
        self.assertEqual(fetch_groups.call_count, 2)
        refresh.assert_called_once_with("https://sub2api.example", "old-refresh")

    def test_sub2api_key_list_uses_browser_first_refresh_fallback(self):
        keys_payload = {
            "success": True,
            "data": {"items": [{"id": 1, "key": "sk-current"}]},
        }
        with patch.object(
            app,
            "fetch_sub2api_keys_by_token",
            side_effect=[
                (False, {"status": 401}, "HTTP 401"),
                (True, keys_payload, None),
            ],
        ) as fetch_keys, patch.object(
            app,
            "refresh_sub2api_auth",
            return_value=(
                True,
                {
                    "access_token": "rotated-access",
                    "refresh_token": "rotated-refresh",
                    "expires_in": 3600,
                },
                None,
            ),
        ) as refresh, patch.object(app, "sub2api_login") as login:
            ok, payload, error = app.fetch_sub2api_keys(
                "https://sub2api.example",
                username="user@example.com",
                password="saved-password",
                auth_mode="browser",
                access_token="expired-access",
                refresh_token="old-refresh",
            )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(payload["data"]["items"][0]["key"], "sk-current")
        self.assertEqual(
            [call.args[1] for call in fetch_keys.call_args_list],
            ["expired-access", "rotated-access"],
        )
        refresh.assert_called_once_with(
            "https://sub2api.example", "expired-access", "old-refresh"
        )
        login.assert_not_called()

    def test_key_group_name_survives_temporarily_unavailable_group(self):
        name = app.sub2api_key_group_name(
            {
                "group_id": 12,
                "group": {"id": 12, "name": "expired-subscription", "rate_multiplier": 0.2},
            },
            {},
        )

        self.assertEqual(name, "expired-subscription")


if __name__ == "__main__":
    unittest.main()
