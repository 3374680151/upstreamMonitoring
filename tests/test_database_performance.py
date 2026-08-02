import os
import threading
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import app


class FakeConnection:
    def __init__(self, rollback_error=False):
        self.rollback_error = rollback_error
        self.rollback_calls = 0
        self.ping_calls = 0
        self.ping_reconnect_values = []
        self.close_calls = 0

    def ping(self, reconnect=False):
        self.ping_calls += 1
        self.ping_reconnect_values.append(reconnect)

    def rollback(self):
        self.rollback_calls += 1
        if self.rollback_error:
            raise RuntimeError("broken")

    def close(self):
        self.close_calls += 1


class DatabasePerformanceTests(unittest.TestCase):
    def test_env_int_uses_default_and_clamps_range(self):
        self.assertTrue(hasattr(app, "_env_int"), "missing runtime parser")
        with patch.dict(os.environ, {"PERF_TEST_INT": "invalid"}):
            self.assertEqual(app._env_int("PERF_TEST_INT", 8, 1, 64), 8)
        with patch.dict(os.environ, {"PERF_TEST_INT": "999"}):
            self.assertEqual(app._env_int("PERF_TEST_INT", 8, 1, 64), 64)
        with patch.dict(os.environ, {"PERF_TEST_INT": "0"}):
            self.assertEqual(app._env_int("PERF_TEST_INT", 8, 1, 64), 1)

    def test_pool_reuses_connection_and_rolls_back_between_leases(self):
        self.assertTrue(hasattr(app, "DatabaseConnectionPool"), "missing pool")
        created = []
        pool = app.DatabaseConnectionPool(
            lambda: created.append(FakeConnection()) or created[-1],
            size=1,
            acquire_timeout=0.05,
        )
        with pool.connection() as first:
            pass
        with pool.connection() as second:
            pass
        self.assertIs(first, second)
        self.assertEqual(len(created), 1)
        self.assertEqual(first.rollback_calls, 2)
        self.assertEqual(first.ping_calls, 1)
        self.assertEqual(first.ping_reconnect_values, [False])

    def test_pool_is_bounded_and_times_out(self):
        self.assertTrue(hasattr(app, "DatabaseConnectionPool"), "missing pool")
        created = []
        pool = app.DatabaseConnectionPool(
            lambda: created.append(FakeConnection()) or created[-1],
            size=1,
            acquire_timeout=0.02,
        )
        with pool.connection():
            with self.assertRaisesRegex(TimeoutError, "数据库连接池繁忙"):
                with pool.connection():
                    pass
        self.assertEqual(len(created), 1)

    def test_pool_discards_connection_that_cannot_rollback(self):
        self.assertTrue(hasattr(app, "DatabaseConnectionPool"), "missing pool")
        created = []

        def factory():
            connection = FakeConnection(rollback_error=not created)
            created.append(connection)
            return connection

        pool = app.DatabaseConnectionPool(factory, size=1, acquire_timeout=0.05)
        with pool.connection() as broken:
            pass
        with pool.connection() as replacement:
            pass
        self.assertIsNot(broken, replacement)
        self.assertEqual(broken.close_calls, 1)
        self.assertEqual(len(created), 2)

    def test_pool_closes_connection_returned_after_shutdown(self):
        connection = FakeConnection()
        pool = app.DatabaseConnectionPool(
            lambda: connection,
            size=1,
            acquire_timeout=0.05,
        )

        with pool.connection():
            pool.close()

        self.assertEqual(connection.close_calls, 1)

    def test_concurrent_reads_can_overlap(self):
        active = 0
        max_active = 0
        state_lock = threading.Lock()
        both_active = threading.Event()

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, _sql, _params):
                nonlocal active, max_active
                with state_lock:
                    active += 1
                    max_active = max(max_active, active)
                    if active == 2:
                        both_active.set()
                both_active.wait(0.15)
                with state_lock:
                    active -= 1

            def fetchone(self):
                return {"value": 1}

        class Connection(FakeConnection):
            def cursor(self):
                return Cursor()

        @contextmanager
        def fake_db_connection():
            yield Connection()

        with patch.object(app, "connect_db", side_effect=Connection), \
             patch.object(app, "db_connection", fake_db_connection, create=True):
            threads = [
                threading.Thread(target=app.db_query_one, args=("SELECT 1",))
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(max_active, 2)

    def test_db_execute_preserves_write_error_when_rollback_fails(self):
        class Cursor:
            lastrowid = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, _sql, _params):
                raise RuntimeError("write failed")

        class Connection(FakeConnection):
            def cursor(self):
                return Cursor()

            def rollback(self):
                raise RuntimeError("rollback failed")

        @contextmanager
        def fake_db_connection():
            yield Connection()

        with patch.object(app, "db_connection", fake_db_connection):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                app.db_execute("UPDATE sites SET enabled = 1")

    def test_site_list_reuses_one_leased_connection(self):
        leased = object()

        @contextmanager
        def fake_db_connection():
            yield leased

        site = {
            "id": 1,
            "name": "one",
            "base_url": "https://example.test",
            "platform": "newapi",
            "enabled": 1,
            "interval_minutes": 3,
            "status": "ok",
            "last_error": None,
            "last_check_at": None,
            "next_check_at": None,
            "consecutive_failures": 0,
            "current_groups_json": "{}",
            "current_login_groups_json": "{}",
        }
        with patch.object(app, "db_connection", fake_db_connection), \
             patch.object(app, "db_query_all", return_value=[site]) as query_all, \
             patch.object(app, "db_query_one", return_value=None) as query_one:
            payload = app.list_sites_payload()

        self.assertEqual(payload[0]["id"], 1)
        self.assertIs(query_all.call_args.kwargs.get("connection"), leased)
        self.assertEqual(query_one.call_count, 2)
        self.assertTrue(
            all(
                call.kwargs.get("connection") is leased
                for call in query_one.call_args_list
            )
        )

    def test_slow_request_log_omits_query_string_and_secrets(self):
        self.assertTrue(
            hasattr(app, "_slow_request_log_line"), "missing slow log helper"
        )
        line = app._slow_request_log_line(
            "GET",
            "/api/sites/1/perf-metrics?model=secret-token",
            200,
            750.4,
            500,
        )
        self.assertEqual(
            line, "[慢请求] GET /api/sites/1/perf-metrics 200 750.4ms"
        )
        self.assertNotIn("secret-token", line)
        self.assertIsNone(
            app._slow_request_log_line("GET", "/api/sites", 200, 99, 500)
        )
        self.assertIsNone(
            app._slow_request_log_line("GET", "/api/sites", 200, 999, 0)
        )

    def test_handler_returns_sanitized_json_when_database_pool_is_busy(self):
        handler = object.__new__(app.Handler)

        def raise_pool_timeout(instance):
            instance.command = "GET"
            instance.path = "/api/overview?token=secret-token"
            raise getattr(app, "DatabasePoolTimeoutError", TimeoutError)(
                "数据库连接池繁忙，请稍后重试"
            )

        with patch.object(
            app.BaseHTTPRequestHandler,
            "handle_one_request",
            autospec=True,
            side_effect=raise_pool_timeout,
        ), patch.object(app, "json_response") as response:
            try:
                handler.handle_one_request()
            except TimeoutError:
                pass

        response.assert_called_once_with(
            handler,
            {
                "success": False,
                "message": "数据库连接池繁忙，请稍后重试",
                "code": "database_busy",
            },
            503,
        )

    def test_close_database_pool_delegates_to_pool(self):
        self.assertTrue(
            hasattr(app, "close_database_pool"), "missing pool shutdown hook"
        )
        with patch.object(app.DB_POOL, "close") as close:
            app.close_database_pool()
        close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
