import io
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import app


class ChannelDiscoveryImportTests(unittest.TestCase):
    def test_group_newapi_channels_by_normalized_base_url(self):
        channels = [
            {"id": 12, "name": "A", "base_url": "https://provider.example/"},
            {"id": 18, "name": "A backup", "base_url": "https://provider.example"},
            {"id": 21, "name": "B", "base_url": "https://other.example/api"},
            {"id": 22, "name": "invalid", "base_url": "ftp://bad.example"},
            {"id": 23, "name": "userinfo", "base_url": "https://u:p@bad.example"},
            {"id": 24, "name": "empty", "base_url": ""},
        ]

        result = app.aggregate_newapi_channel_candidates(channels)

        self.assertEqual(
            result,
            [
                {
                    "base_url": "https://provider.example",
                    "name": "A",
                    "channel_ids": [12, 18],
                    "channel_names": ["A", "A backup"],
                    "channel_count": 2,
                },
                {
                    "base_url": "https://other.example/api",
                    "name": "B",
                    "channel_ids": [21],
                    "channel_names": ["B"],
                    "channel_count": 1,
                },
            ],
        )

    def test_discovery_link_ddl_is_incremental_and_cascades_local_rows(self):
        ddl = next(
            statement
            for statement in app.DDL_STATEMENTS
            if "site_discovery_links" in statement
        )
        self.assertIn("CREATE TABLE IF NOT EXISTS site_discovery_links", ddl)
        self.assertIn("REFERENCES sites(id) ON DELETE CASCADE", ddl)
        self.assertIn("REFERENCES admin_sites(id) ON DELETE CASCADE", ddl)
        self.assertIn("UNIQUE KEY uq_site_discovery_channel", ddl)

    def test_enrich_candidates_marks_existing_site_without_returning_credentials(self):
        candidates = [
            {
                "base_url": "https://provider.example",
                "name": "A",
                "channel_ids": [12],
                "channel_names": ["A"],
                "channel_count": 1,
            }
        ]
        with patch.object(
            app,
            "db_query_all",
            return_value=[
                {
                    "id": 7,
                    "base_url": "https://provider.example/",
                    "status": "ok",
                    "session_sync_status": "ready",
                    "access_token": "secret",
                    "login_password": "secret-password",
                }
            ],
        ):
            result = app.enrich_channel_candidates_with_sites(candidates)

        self.assertEqual(result[0]["existing_site_id"], 7)
        self.assertEqual(result[0]["existing_site_status"], "ok")
        self.assertTrue(result[0]["importable"])
        self.assertNotIn("access_token", result[0])
        self.assertNotIn("login_password", result[0])

    def test_enrich_exposes_token_metadata_for_newapi_sites(self):
        with patch.object(
            app,
            "db_query_all",
            return_value=[
                {
                    "id": 8,
                    "base_url": "https://provider.example",
                    "platform": "newapi",
                    "status": "warning",
                    "auth_mode": "browser",
                    "enabled": 1,
                    "session_sync_status": "no_session",
                    "browser_refresh_cookie": "secret",
                    "access_token": "secret",
                }
            ],
        ):
            result = app.enrich_channel_candidates_with_sites(
                [
                    {
                        "base_url": "https://provider.example",
                        "name": "Provider",
                        "channel_ids": [1],
                        "channel_names": ["Provider"],
                        "channel_count": 1,
                    }
                ]
            )
        candidate = result[0]
        self.assertEqual(candidate["existing_site_auth_mode"], "token")
        self.assertEqual(candidate["existing_site_session_sync_status"], "not_requested")
        self.assertTrue(candidate["existing_site_enabled"])
        self.assertNotIn("browser_refresh_cookie", candidate)
        self.assertNotIn("access_token", candidate)

    def test_fetch_all_newapi_channels_rejects_later_page_failure(self):
        with patch.object(
            app,
            "fetch_newapi_channels",
            side_effect=[
                (True, {"success": True, "data": [{"id": 1}]}, None),
                (False, {}, "upstream timeout"),
            ],
        ):
            ok, items, error = app.fetch_all_newapi_channels(
                {"id": 3, "base_url": "https://admin.example"},
                page_size=1,
                max_pages=3,
            )
        self.assertFalse(ok)
        self.assertEqual(items, [])
        self.assertIn("timeout", error or "")

    def test_import_caps_new_site_interval_without_touching_existing_site_settings(self):
        captured = []

        class _FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params=()):
                captured.append((sql, params))
                self._lastrowid = 0

            @property
            def lastrowid(self):
                return 9

            def fetchone(self):
                return None

        class _FakeConnection:
            def cursor(self):
                return _FakeCursor()

            def commit(self):
                pass

            def rollback(self):
                pass

        @contextmanager
        def leased_connection():
            yield _FakeConnection()

        with patch.object(app, "db_connection", return_value=leased_connection()):
            result = app.import_discovered_sites(
                {"id": 3, "platform": "newapi"},
                {
                    "interval_minutes": 999999,
                    "items": [
                        {
                            "base_url": "https://provider.example",
                            "name": "Provider",
                            "channel_ids": [1],
                        }
                    ],
                },
            )
        self.assertEqual(result[0]["status"], "created")
        insert_sql, insert_params = next(
            (sql, params) for sql, params in captured if "INSERT INTO sites" in sql
        )
        self.assertIn(app.MAX_DISCOVERY_INTERVAL_MINUTES, insert_params)

    def test_import_candidates_reuses_existing_site_and_writes_link(self):
        admin_site = {"id": 3, "platform": "newapi"}
        body = {
            "interval_minutes": 3,
            "items": [
                {
                    "base_url": "https://provider.example/",
                    "name": "Provider A",
                    "channel_ids": [12],
                }
            ],
        }

        class _FakeCursor:
            def __init__(self):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params=()):
                self.calls.append(sql)
                if "LIMIT 1 FOR UPDATE" in sql or "LIMIT 1" in sql:
                    self._next = {
                        "id": 7,
                        "platform": "newapi",
                        "base_url": "https://provider.example",
                    }
                else:
                    self._next = None

            def fetchone(self):
                return getattr(self, "_next", None)

            @property
            def lastrowid(self):
                return 0

        class _FakeConnection:
            def __init__(self):
                self.cursor_obj = _FakeCursor()
                self.executed = []

            def cursor(self):
                return self.cursor_obj

            def commit(self):
                pass

            def rollback(self):
                pass

        fake = _FakeConnection()

        @contextmanager
        def leased_connection():
            yield fake

        with patch.object(app, "db_connection", return_value=leased_connection()):
            result = app.import_discovered_sites(admin_site, body)

        self.assertEqual(result[0]["site_id"], 7)
        self.assertIn(result[0]["status"], {"created", "existing"})
        self.assertTrue(
            any("site_discovery_links" in sql for sql in fake.cursor_obj.calls)
        )

    def test_import_rejects_non_newapi_admin_site(self):
        result = app.import_discovered_sites(
            {"id": 3, "platform": "sub2api"},
            {"items": [{"base_url": "https://provider.example", "channel_ids": [1]}]},
        )
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("error"), "platform_invalid")

    def test_import_marks_invalid_url_without_writing(self):
        with patch.object(app, "db_query_one") as query, patch.object(
            app, "db_execute"
        ) as execute:
            result = app.import_discovered_sites(
                {"id": 3, "platform": "newapi"},
                {
                    "items": [
                        {"base_url": "ftp://provider.example", "channel_ids": [1]},
                        {"base_url": "https://u:p@provider.example", "channel_ids": [2]},
                    ]
                },
            )
        self.assertTrue(all(item["status"] == "invalid" for item in result))
        query.assert_not_called()
        execute.assert_not_called()

    def test_get_channel_candidates_route_returns_enriched_payload(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/admin/sites/3/channel-candidates"
        handler.headers = {}
        site = {"id": 3, "platform": "newapi"}
        channels = [{"id": 1, "name": "A", "base_url": "https://a.example"}]
        candidates = [{"base_url": "https://a.example", "channel_ids": [1]}]
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "get_admin_site_or_404", return_value=(site, None, 200)
        ), patch.object(
            app, "fetch_admin_site_channels", return_value=(True, channels, {"total": 1}, None)
        ) as fetch, patch.object(
            app, "aggregate_newapi_channel_candidates", return_value=candidates
        ), patch.object(
            app, "enrich_channel_candidates_with_sites", return_value=candidates
        ), patch.object(app, "json_response") as response:
            app.Handler.do_GET(handler)

        fetch.assert_called_once_with(site, "")
        payload = response.call_args.args[1]
        self.assertTrue(payload["success"])
        self.assertEqual(payload["meta"]["total"], 1)
        self.assertEqual(payload["meta"]["source_channel_total"], 1)

    def test_discovery_import_route_calls_importer_before_generic_sites_routes(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/sites/discovery-import"
        handler.headers = {"Content-Length": "2"}
        handler.rfile = io.BytesIO(b"{}")
        body = {"admin_site_id": 3, "items": []}
        result = [{"status": "existing", "site_id": 7}]
        with patch.object(app.Handler, "_auth_guard", return_value=False), patch.object(
            app, "read_json_body", return_value=body
        ), patch.object(
            app, "get_admin_site_or_404", return_value=({"id": 3, "platform": "newapi"}, None, 200)
        ), patch.object(
            app, "import_discovered_sites", return_value=result
        ) as importer, patch.object(app, "json_response") as response:
            app.Handler.do_POST(handler)

        importer.assert_called_once_with({"id": 3, "platform": "newapi"}, body)
        self.assertEqual(response.call_args.args[1], {"success": True, "data": result})

    def test_list_discovery_links_returns_only_public_source_fields(self):
        rows = [
            {
                "site_id": 7,
                "admin_site_id": 3,
                "admin_site_name": "主站 A",
                "channel_id": 12,
                "channel_name": "主渠道",
                "upstream_base_url": "https://provider.example",
                "created_at": "2026-08-02T10:00:00+08:00",
                "updated_at": "2026-08-02T10:00:00+08:00",
                "access_token": "must-not-leak",
                "login_password": "must-not-leak",
                "browser_refresh_cookie": "must-not-leak",
            }
        ]
        with patch.object(app, "db_query_all", return_value=rows):
            result = app.list_site_discovery_links(7)
        self.assertEqual(result[0]["admin_site_name"], "主站 A")
        self.assertEqual(result[0]["channel_name"], "主渠道")
        self.assertEqual(result[0]["upstream_base_url"], "https://provider.example")
        self.assertNotIn("access_token", result[0])
        self.assertNotIn("login_password", result[0])
        self.assertNotIn("browser_refresh_cookie", result[0])

    def test_reconcile_discovery_links_removes_only_stale_links(self):
        rows = [
            {"id": 10, "channel_id": 12, "upstream_base_url": "https://a.example"},
            {"id": 11, "channel_id": 18, "upstream_base_url": "https://old.example"},
        ]
        candidates = [
            {
                "base_url": "https://a.example",
                "channel_ids": [12],
                "channel_names": ["A"],
            }
        ]
        with patch.object(app, "db_query_all", return_value=rows), patch.object(
            app, "db_execute_rowcount", return_value=1
        ) as execute:
            removed = app.reconcile_site_discovery_links(3, candidates)
        self.assertEqual(removed, 1)
        deleted = [call.args[0] for call in execute.call_args_list]
        self.assertTrue(all("DELETE FROM site_discovery_links" in sql for sql in deleted))
        # never touches sites / snapshots / changes
        joined = "\n".join(deleted)
        self.assertNotIn("DELETE FROM sites", joined)
        self.assertNotIn("DELETE FROM snapshots", joined)
        self.assertNotIn("DELETE FROM changes", joined)
        # only deletes the stale id (11) for this admin site
        self.assertEqual(execute.call_args.args[1], (11, 3))

    def test_import_rolls_back_created_site_when_link_insert_fails(self):
        class _FakeCursor:
            def __init__(self):
                self.queries = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params=()):
                self.queries.append(sql)
                self._next = None

            def fetchone(self):
                return getattr(self, "_next", None)

            @property
            def lastrowid(self):
                return 7

        class _FakeConnection:
            def __init__(self):
                self.cursor_obj = _FakeCursor()
                self.commits = 0
                self.rollbacks = 0

            def cursor(self):
                return self.cursor_obj

            def commit(self):
                self.commits += 1

            def rollback(self):
                self.rollbacks += 1

        fake = _FakeConnection()

        @contextmanager
        def leased_connection():
            yield fake

        with patch.object(app, "db_connection", return_value=leased_connection()), patch.object(
            app,
            "_import_discovered_site_item",
            side_effect=RuntimeError("link failed"),
        ):
            result = app.import_discovered_sites(
                {"id": 3, "platform": "newapi"},
                {
                    "items": [
                        {
                            "base_url": "https://provider.example",
                            "name": "Provider",
                            "channel_ids": [12],
                            "channel_names": ["主渠道"],
                        }
                    ]
                },
            )
        self.assertEqual(result[0]["status"], "conflict")
        self.assertEqual(fake.commits, 0)
        self.assertEqual(fake.rollbacks, 1)

    def test_import_discovered_site_item_writes_link_with_channel_name(self):
        class _FakeCursor:
            def __init__(self):
                self.queries = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params=()):
                self.queries.append((sql, params))
                self._next = None

            def fetchone(self):
                return getattr(self, "_next", None)

            @property
            def lastrowid(self):
                return 12

        class _FakeConnection:
            def __init__(self):
                self.cursor_obj = _FakeCursor()

            def cursor(self):
                return self.cursor_obj

            def commit(self):
                pass

            def rollback(self):
                pass

        fake = _FakeConnection()
        result = app._import_discovered_site_item(
            fake,
            3,
            {
                "base_url": "https://provider.example",
                "name": "Provider",
                "channel_ids": [12],
                "channel_names": ["主渠道"],
            },
            5,
        )
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["site_id"], 12)
        link_sql, link_params = next(
            (sql, params) for sql, params in fake.cursor_obj.queries
            if "INSERT INTO site_discovery_links" in sql
        )
        self.assertEqual(link_params[4], "主渠道")

    def test_auto_sync_imports_pending_candidates_only(self):
        admin_site = {"id": 3, "platform": "newapi", "base_url": "https://upstream.example"}
        fetched_channels = [
            {"id": 11, "name": "A", "base_url": "https://provider.example"},
            {"id": 12, "name": "B", "base_url": "https://already-imported.example"},
        ]
        existing_urls = [{"base_url": "https://already-imported.example"}]

        captured: dict = {}

        def fake_import(admin, body):
            captured["body"] = body
            return [
                {
                    "status": "created",
                    "base_url": "https://provider.example",
                    "name": "A",
                    "channel_ids": [11],
                }
            ]

        with patch.object(app, "db_query_all", side_effect=[[admin_site], existing_urls]), \
             patch.object(
                 app, "fetch_admin_site_channels",
                 return_value=(True, fetched_channels, {"total": 2}, None),
             ), patch.object(
                 app, "reconcile_site_discovery_links", return_value=0
             ), patch.object(
                 app, "import_discovered_sites", side_effect=fake_import
             ):
            results = app.auto_sync_admin_site_channels_to_sites()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "imported")
        self.assertEqual(results[0]["imported"], 1)
        # Only the pending candidate should have been sent to import.
        items = captured["body"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["base_url"], "https://provider.example")

    def test_auto_sync_marks_already_synced_when_no_pending_candidates(self):
        admin_site = {"id": 4, "platform": "newapi", "base_url": "https://upstream.example"}
        fetched_channels = [
            {"id": 21, "name": "X", "base_url": "https://already-imported.example"},
        ]
        existing_urls = [{"base_url": "https://already-imported.example"}]

        with patch.object(app, "db_query_all", side_effect=[[admin_site], existing_urls]), \
             patch.object(
                 app, "fetch_admin_site_channels",
                 return_value=(True, fetched_channels, {"total": 1}, None),
             ), patch.object(
                 app, "reconcile_site_discovery_links", return_value=0
             ):
            results = app.auto_sync_admin_site_channels_to_sites()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "already_synced")
        self.assertEqual(results[0]["imported"], 0)

    def test_auto_sync_isolates_failures_per_admin_site(self):
        admin_a = {"id": 5, "platform": "newapi", "base_url": "https://a.example"}
        admin_b = {"id": 6, "platform": "newapi", "base_url": "https://b.example"}

        def fake_fetch(admin, keyword):
            if int(admin["id"]) == 5:
                return False, [], {}, "upstream 502"
            return True, [], {"total": 0}, None

        with patch.object(app, "db_query_all", return_value=[admin_a, admin_b]), \
             patch.object(app, "fetch_admin_site_channels", side_effect=fake_fetch), \
             patch.object(app, "reconcile_site_discovery_links", return_value=0):
            results = app.auto_sync_admin_site_channels_to_sites()

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["status"], "fetch_failed")
        self.assertEqual(results[1]["status"], "no_channels")

    def test_auto_sync_skips_sub2api_admin_sites(self):
        sub2api_admin = {"id": 7, "platform": "sub2api", "base_url": "https://sub.example"}

        with patch.object(app, "db_query_all", return_value=[sub2api_admin]), \
             patch.object(app, "fetch_admin_site_channels") as fetch:
            results = app.auto_sync_admin_site_channels_to_sites()

        self.assertEqual(results, [])
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
