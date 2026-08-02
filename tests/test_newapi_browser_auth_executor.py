"""Regression tests for the unified NewAPI browser request executor and
the CAS write protection added by the remaining-work design document.

These tests cover the 13 acceptance criteria from
``docs/browser-session-auth-remaining-work.md``.
"""

import unittest
from unittest.mock import patch

import app


class _ExecutorFixtures:
    @staticmethod
    def site(auth_mode="token", **overrides):
        site = {
            "id": 7,
            "base_url": "https://newapi.example",
            "platform": "newapi",
            "auth_mode": auth_mode,
            "access_token": "system-access",
            "access_user_id": "21",
            "browser_session_id": "session-21",
            "browser_refresh_cookie": "new_api_refresh=refresh-token",
            "browser_access_expires_at": 4102444800,
        }
        site.update(overrides)
        return site


class NewApiBrowserAuthExecutorTests(unittest.TestCase, _ExecutorFixtures):
    def test_finish_session_sync_request_does_not_touch_sites_when_request_already_terminal(self):
        row = {
            "id": "stale",
            "site_id": 3,
            "admin_site_id": None,
            "platform": "sub2api",
            "target_origin": "https://example.com",
            "status": "expired",
        }
        with patch.object(app, "db_query_one", return_value=row), patch.object(
            app, "db_execute_rowcount", return_value=0
        ) as rowcount, patch.object(app, "db_execute") as execute:
            finished = app.finish_session_sync_request("stale", "ready")

        self.assertFalse(finished)
        # The terminal write on the request row is the only DB call; the site
        # row is never touched on a CAS miss.
        self.assertEqual(rowcount.call_count, 1)
        execute.assert_not_called()

    def test_finish_session_sync_request_skips_admin_site_when_newer_request_claimed(self):
        row = {
            "id": "old",
            "site_id": None,
            "admin_site_id": 13,
            "platform": "newapi",
            "target_origin": "https://newapi.example",
            "status": "validating",
        }
        with patch.object(
            app,
            "db_query_one",
            side_effect=[row, {"id": "newer"}],
        ), patch.object(
            app, "db_execute_rowcount", return_value=1
        ) as rowcount:
            finished = app.finish_session_sync_request("old", "ready")

        self.assertTrue(finished)
        # Only the request row was updated; the admin site update is skipped
        # because a newer request has taken over.
        self.assertEqual(rowcount.call_count, 1)

    def test_newapi_site_cas_function_uses_validating_request_condition(self):
        session = {
            "access_token": "dashboard-access",
            "access_user_id": "21",
            "browser_refresh_cookie": "new_api_refresh=token",
            "browser_session_id": "session-21",
            "browser_access_expires_at": 4102444800,
        }
        with patch.object(app, "db_execute_rowcount", return_value=1) as rowcount:
            ok = app.persist_newapi_site_browser_session_cas(
                7, session, "newapi-request", "https://newapi.example"
            )

        self.assertTrue(ok)
        sql, _params = rowcount.call_args.args
        self.assertIn("UPDATE sites AS s", sql)
        self.assertIn("s.platform = 'newapi'", sql)
        self.assertIn("s.auth_mode = 'browser'", sql)
        self.assertIn("r.status = 'validating'", sql)
        self.assertIn("r.site_id = s.id", sql)
        self.assertIn("r.admin_site_id IS NULL", sql)

    def test_newapi_site_cas_returns_false_when_no_row_updated(self):
        session = {
            "access_token": "dashboard-access",
            "access_user_id": "21",
            "browser_session_id": "session-21",
            "browser_refresh_cookie": "new_api_refresh=token",
            "browser_access_expires_at": 4102444800,
        }
        with patch.object(app, "db_execute_rowcount", return_value=0):
            ok = app.persist_newapi_site_browser_session_cas(
                7, session, "stale", "https://newapi.example"
            )
        self.assertFalse(ok)

    def test_admin_browser_auth_cas_never_writes_system_credentials(self):
        admin_site = {
            "id": 13,
            "base_url": "https://newapi.example",
            "access_token": "system-token",
            "access_user_id": "admin-id",
            "security_proof": "two-factor-proof",
        }
        with patch.object(app, "db_execute_rowcount", return_value=1) as rowcount:
            ok = app.persist_admin_browser_auth_cas(
                admin_site,
                request_id="newapi-request",
                expected_origin="https://newapi.example",
                access_token="dashboard-access",
                refresh_cookie="new_api_refresh=refresh",
                session_id="session-21",
                access_expires_at=4102444800,
            )

        self.assertTrue(ok)
        sql, _params = rowcount.call_args.args
        self.assertIn("browser_access_token = ?", sql)
        self.assertIn("browser_refresh_cookie = ?", sql)
        self.assertIn("browser_session_id = ?", sql)
        # System credentials must never be written by the CAS path.  The
        # pattern below is intentionally anchored to the column being set,
        # not the SET-list as a whole (which also contains
        # ``browser_access_token``).
        self.assertNotRegex(sql, r"(?<!browser_)access_token\s*=\s*\?")
        self.assertNotIn("security_proof", sql)
        # The in-memory admin row keeps its system credentials untouched.
        self.assertEqual(admin_site["access_token"], "system-token")
        self.assertEqual(admin_site["security_proof"], "two-factor-proof")

    def test_admin_browser_auth_cas_returns_false_without_modifying_row(self):
        admin_site = {
            "id": 13,
            "base_url": "https://newapi.example",
            "access_token": "system-token",
            "security_proof": "two-factor-proof",
        }
        with patch.object(app, "db_execute_rowcount", return_value=0):
            ok = app.persist_admin_browser_auth_cas(
                admin_site,
                request_id="stale",
                expected_origin="https://newapi.example",
                access_token="dashboard-access",
                refresh_cookie="new_api_refresh=refresh",
                session_id="session-21",
                access_expires_at=4102444800,
            )
        self.assertFalse(ok)
        self.assertEqual(admin_site["access_token"], "system-token")

    def test_browser_request_401_triggers_exactly_one_forced_refresh_and_retry(self):
        site = self.site(auth_mode="browser")
        with patch.object(
            app, "ensure_newapi_site_browser_session", return_value=(True, None)
        ), patch.object(app, "db_query_one", return_value=None), patch.object(
            app,
            "request_json",
            side_effect=[
                (False, {"status": 401, "raw": ""}, "HTTP 401"),
                (True, {"success": True, "data": []}, None),
            ],
        ) as request_json, patch.object(
            app, "refresh_newapi_site_browser_session", return_value=(True, None)
        ) as refresh:
            ok, payload, error = app.newapi_browser_request(
                site, "GET", "/api/user/self"
            )

        self.assertTrue(ok)
        self.assertEqual(request_json.call_count, 2)
        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.kwargs.get("force"), True)

    def test_browser_request_403_triggers_exactly_one_forced_refresh_and_retry(self):
        site = self.site(auth_mode="browser")
        with patch.object(
            app, "ensure_newapi_site_browser_session", return_value=(True, None)
        ), patch.object(app, "db_query_one", return_value=None), patch.object(
            app,
            "request_json",
            side_effect=[
                (False, {"status": 403, "raw": ""}, "HTTP 403"),
                (True, {"success": True, "data": []}, None),
            ],
        ), patch.object(
            app, "refresh_newapi_site_browser_session", return_value=(True, None)
        ) as refresh:
            ok, _payload, _error = app.newapi_browser_request(
                site, "GET", "/api/user/groups"
            )

        self.assertTrue(ok)
        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.kwargs.get("force"), True)

    def test_browser_request_does_not_refresh_on_5xx_429_or_network_error(self):
        site = self.site(auth_mode="browser")
        for transport_payload, transport_error in [
            ({"status": 503, "raw": ""}, "HTTP 503"),
            ({"status": 429, "raw": ""}, "HTTP 429"),
            ({"error": "dns"}, "dns"),
        ]:
            with self.subTest(transport_error=transport_error), patch.object(
                app, "ensure_newapi_site_browser_session", return_value=(True, None)
            ), patch.object(app, "db_query_one", return_value=None), patch.object(
                app,
                "request_json",
                return_value=(False, transport_payload, transport_error),
            ), patch.object(
                app, "refresh_newapi_site_browser_session"
            ) as refresh:
                ok, _payload, _error = app.newapi_browser_request(
                    site, "GET", "/api/user/self"
                )
            self.assertFalse(ok)
            refresh.assert_not_called()

    def test_browser_request_retry_still_401_returns_redacted_message(self):
        site = self.site(auth_mode="browser")
        with patch.object(
            app, "ensure_newapi_site_browser_session", return_value=(True, None)
        ), patch.object(app, "db_query_one", return_value=None), patch.object(
            app,
            "request_json",
            side_effect=[
                (False, {"status": 401, "raw": ""}, "HTTP 401"),
                (False, {"status": 401, "raw": ""}, "HTTP 401"),
            ],
        ), patch.object(
            app, "refresh_newapi_site_browser_session", return_value=(True, None)
        ):
            ok, _payload, error = app.newapi_browser_request(
                site, "GET", "/api/user/self"
            )

        self.assertFalse(ok)
        self.assertIn("网页登录", error)
        # No real token, cookie or session id in the error message.
        self.assertNotIn("system-access", error)
        self.assertNotIn("refresh-token", error)
        self.assertNotIn("session-21", error)

    def test_fetch_newapi_user_token_list_routes_through_browser_executor(self):
        site = self.site(auth_mode="browser")
        with patch.object(
            app, "ensure_newapi_site_browser_session", return_value=(True, None)
        ), patch.object(app, "db_query_one", return_value=None), patch.object(
            app, "db_query_all", return_value=[]
        ), patch.object(
            app,
            "request_json",
            return_value=(True, {"success": True, "data": {"items": []}}, None),
        ) as request_json:
            ok, _items, _err = app.fetch_all_newapi_user_tokens(site)

        self.assertTrue(ok)
        first_call = request_json.call_args
        self.assertIn("/api/token/", first_call.args[0])
        headers = first_call.kwargs["headers"]
        self.assertIn("Bearer", headers.get("Authorization", ""))
        self.assertEqual(headers.get("X-Auth-Session"), "session-21")
        self.assertEqual(headers.get("Cookie"), "new_api_refresh=refresh-token")

    def test_fetch_newapi_user_token_key_routes_through_browser_executor(self):
        site = self.site(auth_mode="browser")
        with patch.object(
            app, "ensure_newapi_site_browser_session", return_value=(True, None)
        ), patch.object(app, "db_query_one", return_value=None), patch.object(
            app,
            "request_json",
            return_value=(
                True,
                {"success": True, "data": {"key": "sk-abcdef"}},
                None,
            ),
        ) as request_json:
            ok, key, _err = app.fetch_newapi_user_token_key(site, 42)

        self.assertTrue(ok)
        self.assertEqual(key, "sk-abcdef")
        self.assertEqual(request_json.call_args.kwargs.get("method"), "POST")
        self.assertIn("/api/token/42/key", request_json.call_args.args[0])
        headers = request_json.call_args.kwargs["headers"]
        self.assertEqual(headers.get("X-Auth-Session"), "session-21")

    def test_fetch_newapi_pricing_for_site_uses_browser_executor(self):
        site = self.site(auth_mode="browser")
        with patch.object(
            app, "ensure_newapi_site_browser_session", return_value=(True, None)
        ), patch.object(app, "db_query_one", return_value=None), patch.object(
            app,
            "request_json",
            return_value=(
                True,
                {"success": True, "data": {"model_groups": []}},
                None,
            ),
        ) as request_json:
            ok, _payload, _err = app.fetch_newapi_pricing_for_site(site)

        self.assertTrue(ok)
        self.assertIn("/api/pricing", request_json.call_args.args[0])
        headers = request_json.call_args.kwargs["headers"]
        self.assertEqual(headers.get("X-Auth-Session"), "session-21")
        self.assertEqual(headers.get("Cookie"), "new_api_refresh=refresh-token")

    def test_fetch_newapi_perf_summary_for_site_uses_browser_executor(self):
        site = self.site(auth_mode="browser")
        with patch.object(
            app, "ensure_newapi_site_browser_session", return_value=(True, None)
        ), patch.object(app, "db_query_one", return_value=None), patch.object(
            app,
            "request_json",
            return_value=(True, {"success": True, "data": []}, None),
        ) as request_json:
            ok, _payload, _err = app.fetch_newapi_perf_summary_for_site(site, 12)

        self.assertTrue(ok)
        self.assertIn("/api/perf-metrics/summary", request_json.call_args.args[0])
        self.assertIn("hours=12", request_json.call_args.args[0])

    def test_fetch_newapi_perf_detail_for_site_uses_browser_executor(self):
        site = self.site(auth_mode="browser")
        with patch.object(
            app, "ensure_newapi_site_browser_session", return_value=(True, None)
        ), patch.object(app, "db_query_one", return_value=None), patch.object(
            app,
            "request_json",
            return_value=(True, {"success": True, "data": []}, None),
        ) as request_json:
            ok, _payload, _err = app.fetch_newapi_perf_detail_for_site(
                site, "gpt-4o", 6, "vip"
            )

        self.assertTrue(ok)
        self.assertIn("/api/perf-metrics", request_json.call_args.args[0])
        self.assertIn("model=gpt-4o", request_json.call_args.args[0])
        self.assertIn("hours=6", request_json.call_args.args[0])
        self.assertIn("group=vip", request_json.call_args.args[0])

    def test_token_request_path_uses_only_token_headers(self):
        site = self.site(auth_mode="token")
        with patch.object(
            app,
            "request_json",
            return_value=(True, {"success": True, "data": []}, None),
        ) as request_json:
            ok, _payload, _err = app.fetch_all_newapi_user_tokens(site)

        self.assertTrue(ok)
        headers = request_json.call_args.kwargs["headers"]
        # token mode must NOT include browser session headers.
        self.assertNotIn("X-Auth-Session", headers)
        self.assertNotIn("Cookie", headers)
        # but it does carry the system New-Api-User header.
        self.assertEqual(headers.get("New-Api-User"), "21")

    def test_token_list_cache_key_never_contains_plaintext_secrets(self):
        site = self.site(
            auth_mode="browser",
            access_token="plaintext-access",
            access_user_id="21",
            browser_refresh_cookie="new_api_refresh=plaintext-cookie",
            browser_session_id="plaintext-session",
            browser_access_expires_at=4102444880,
        )
        cache_key = app._newapi_token_cache_key(site)
        for secret in (
            "plaintext-access",
            "plaintext-cookie",
            "plaintext-session",
            "system-access",
        ):
            self.assertNotIn(secret, cache_key)

    def test_uptime_cache_key_never_contains_plaintext_secrets(self):
        site = self.site(
            auth_mode="browser",
            access_token="plaintext-access",
            browser_refresh_cookie="new_api_refresh=plaintext-cookie",
            browser_session_id="plaintext-session",
        )
        cache_key = app._newapi_uptime_cache_key(site)
        for secret in (
            "plaintext-access",
            "plaintext-cookie",
            "plaintext-session",
            "system-access",
        ):
            self.assertNotIn(secret, cache_key)

    def test_sites_pricing_route_routes_through_browser_executor(self):
        """The /api/sites/:id/pricing route must call the browser-aware fetcher.

        We read the source of ``Handler.do_GET`` directly and assert that
        the route block calls the browser-aware fetcher and not the legacy
        plain-header fetcher.  This is a structural test that catches any
        future regression where the route is rewired to the legacy
        ``fetch_newapi_pricing`` (which uses bare access_token+user_id).
        """
        import inspect
        source = inspect.getsource(app.Handler.do_GET)
        # Locate the /api/sites/:id/pricing block.
        idx = source.find('/api/sites/") and path.endswith("/pricing")')
        self.assertGreater(idx, -1, "pricing route block missing")
        # The block must use the browser-aware fetcher.
        block_start = source.rfind("if", 0, idx)
        block_end = source.find("/api/sites/", idx + 100)
        if block_end == -1:
            block_end = len(source)
        block = source[block_start:block_end]
        self.assertIn("fetch_newapi_pricing_for_site", block)
        self.assertNotIn("fetch_newapi_pricing(", block)

    def test_sites_perf_summary_route_routes_through_browser_executor(self):
        import inspect
        source = inspect.getsource(app.Handler.do_GET)
        idx = source.find('/api/sites/") and path.endswith("/perf-metrics/summary")')
        self.assertGreater(idx, -1, "perf-metrics summary route block missing")
        block_start = source.rfind("if", 0, idx)
        block_end = source.find("/api/sites/", idx + 100)
        if block_end == -1:
            block_end = len(source)
        block = source[block_start:block_end]
        self.assertIn("fetch_newapi_perf_summary_for_site", block)
        self.assertNotIn("fetch_newapi_perf_summary(", block)

    def test_sites_perf_detail_route_routes_through_browser_executor(self):
        import inspect
        source = inspect.getsource(app.Handler.do_GET)
        idx = source.find('"/api/sites/") and ("/perf-metrics" in path)')
        self.assertGreater(idx, -1, "perf-metrics detail route block missing")
        block_start = source.rfind("if", 0, idx)
        block_end = source.find("/api/sites/", idx + 100)
        if block_end == -1:
            block_end = len(source)
        block = source[block_start:block_end]
        self.assertIn("fetch_newapi_perf_detail_for_site", block)
        self.assertNotIn("fetch_newapi_perf_detail(", block)


if __name__ == "__main__":
    unittest.main()
