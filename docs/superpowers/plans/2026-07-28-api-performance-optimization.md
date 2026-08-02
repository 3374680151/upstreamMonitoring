# API Performance Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove local API queueing and repeated MySQL handshakes while preserving real-time upstream reads, existing API contracts, and NewAPI rate-limit safety.

**Architecture:** Add a bounded lazy LIFO pool around independent PyMySQL connections, remove the process-wide lock from ordinary queries, and allow related site-summary reads to share one leased connection. Add bounded runtime timeouts and sanitized slow-request logging; do not cache external main-site data.

**Tech Stack:** Python 3 standard library (`contextlib`, `queue`, `threading`, `unittest`), PyMySQL, stdlib HTTP server, MySQL 8.4, Docker Compose.

---

## File Map

- Create `tests/test_database_performance.py`: pool, concurrency, shared-lease, configuration, and slow-log regressions.
- Modify `app.py`: runtime parsing, connection pool, unlocked database helpers, shared summary connection, slow-request diagnostics, clean shutdown.
- Modify `.env.example`: document performance settings without adding secrets.
- Modify `docker-compose.yml`: pass performance settings into the application container.
- Verify `apps/web` only; no frontend source or API contract changes.

The repository is a shared dirty `master` worktree whose `app.py`, `.env.example`, and
`docker-compose.yml` already contain user changes. Do not commit implementation files
from this plan; preserve them as working-tree changes and report the exact touched files.

### Task 1: Runtime Parsing and Bounded Connection Pool

**Files:**
- Create: `tests/test_database_performance.py`
- Modify: `app.py:1-170`

- [ ] **Step 1: Write failing configuration and pool tests**

Create `tests/test_database_performance.py` with focused fakes and these behaviors:

```python
import os
import threading
import unittest
from unittest.mock import patch

import app


class FakeConnection:
    def __init__(self, rollback_error=False):
        self.rollback_error = rollback_error
        self.rollback_calls = 0
        self.ping_calls = 0
        self.close_calls = 0

    def ping(self, reconnect=False):
        self.ping_calls += 1

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
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_database_performance.DatabasePerformanceTests.test_env_int_uses_default_and_clamps_range tests.test_database_performance.DatabasePerformanceTests.test_pool_reuses_connection_and_rolls_back_between_leases tests.test_database_performance.DatabasePerformanceTests.test_pool_is_bounded_and_times_out tests.test_database_performance.DatabasePerformanceTests.test_pool_discards_connection_that_cannot_rollback -v
```

Expected: failures reporting missing `_env_int` and `DatabaseConnectionPool`.

- [ ] **Step 3: Implement the parser, pool, and MySQL socket settings**

In `app.py`, import `contextmanager`, `Empty`, and `LifoQueue`. Add:

```python
def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


class DatabaseConnectionPool:
    def __init__(self, connection_factory, size: int, acquire_timeout: float):
        self._connection_factory = connection_factory
        self._acquire_timeout = acquire_timeout
        self._slots = LifoQueue(maxsize=size)
        for _ in range(size):
            self._slots.put(None)

    @contextmanager
    def connection(self):
        try:
            connection = self._slots.get(timeout=self._acquire_timeout)
        except Empty as exc:
            raise TimeoutError("数据库连接池繁忙，请稍后重试") from exc
        try:
            if connection is None:
                connection = self._connection_factory()
            else:
                connection.ping(reconnect=True)
            yield connection
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    try:
                        connection.close()
                    except Exception:
                        pass
                    connection = None
            self._slots.put(connection)

    def close(self) -> None:
        while True:
            try:
                connection = self._slots.get_nowait()
            except Empty:
                break
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
```

Define bounded settings before `connect_db()`:

```python
DB_POOL_SIZE = _env_int("DB_POOL_SIZE", 8, 1, 32)
DB_POOL_ACQUIRE_TIMEOUT_SECONDS = _env_int("DB_POOL_ACQUIRE_TIMEOUT", 5, 1, 60)
DB_CONNECT_TIMEOUT_SECONDS = _env_int("DB_CONNECT_TIMEOUT", 5, 1, 60)
DB_READ_TIMEOUT_SECONDS = _env_int("DB_READ_TIMEOUT", 15, 1, 300)
DB_WRITE_TIMEOUT_SECONDS = _env_int("DB_WRITE_TIMEOUT", 15, 1, 300)
HTTP_TIMEOUT_SECONDS = _env_int("UPSTREAM_HTTP_TIMEOUT", 15, 1, 120)
SLOW_REQUEST_THRESHOLD_MS = _env_int("SLOW_REQUEST_THRESHOLD_MS", 500, 0, 60000)
```

Pass `connect_timeout`, `read_timeout`, and `write_timeout` to `pymysql.connect`, then
construct `DB_POOL = DatabaseConnectionPool(lambda: connect_db(), ...)` after
`connect_db()` is defined.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all four tests pass.

### Task 2: Remove Global Query Serialization

**Files:**
- Modify: `tests/test_database_performance.py`
- Modify: `app.py:398-485`

- [ ] **Step 1: Add a failing concurrent-read regression**

Add fakes implementing the cursor context manager and a test that patches both the
current raw connection path and the intended `db_connection` path:

```python
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
```

Import `contextmanager` in the test file.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_database_performance.DatabasePerformanceTests.test_concurrent_reads_can_overlap -v
```

Expected: FAIL because the existing `DB_LOCK` limits `max_active` to one.

- [ ] **Step 3: Lease pooled connections in database helpers**

Add:

```python
@contextmanager
def db_connection():
    with DB_POOL.connection() as connection:
        yield connection
```

Change helpers to accept an optional existing connection and otherwise lease one:

```python
def db_query_one(sql: str, params: Iterable[Any] = (), connection=None):
    if connection is None:
        with db_connection() as leased:
            return db_query_one(sql, params, connection=leased)
    with connection.cursor() as cur:
        cur.execute(_q(sql), tuple(params))
        row = cur.fetchone()
        return dict(row) if row else None
```

Apply the same pattern to `db_query_all`. `db_execute` always leases a connection,
commits on success, and explicitly rolls back then re-raises on failure. Remove
`DB_LOCK` from ordinary helper bodies; retain it only around `init_db()`.

- [ ] **Step 4: Verify GREEN and run database regressions**

Run:

```bash
python3 -m unittest tests.test_database_performance -v
python3 -m unittest discover -s tests -v
```

Expected: the concurrency test and all existing tests pass.

### Task 3: Reuse One Lease for Site Summaries

**Files:**
- Modify: `tests/test_database_performance.py`
- Modify: `app.py:4291-4375`

- [ ] **Step 1: Write a failing shared-connection test**

Patch `db_connection`, `db_query_all`, and `db_query_one`, return one site, and assert
every summary query receives the same connection object:

```python
    def test_site_list_reuses_one_leased_connection(self):
        leased = object()

        @contextmanager
        def fake_db_connection():
            yield leased

        site = {
            "id": 1, "name": "one", "base_url": "https://example.test",
            "platform": "newapi", "enabled": 1, "interval_minutes": 3,
            "status": "ok", "last_error": None, "last_check_at": None,
            "next_check_at": None, "consecutive_failures": 0,
            "current_groups_json": "{}", "current_login_groups_json": "{}",
        }
        with patch.object(app, "db_connection", fake_db_connection), \
             patch.object(app, "db_query_all", return_value=[site]) as query_all, \
             patch.object(app, "db_query_one", return_value=None) as query_one:
            payload = app.list_sites_payload()
        self.assertEqual(payload[0]["id"], 1)
        self.assertIs(query_all.call_args.kwargs["connection"], leased)
        self.assertEqual(query_one.call_count, 2)
        self.assertTrue(all(call.kwargs["connection"] is leased for call in query_one.call_args_list))
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_database_performance.DatabasePerformanceTests.test_site_list_reuses_one_leased_connection -v
```

Expected: FAIL because `list_sites_payload()` does not lease once or pass a connection.

- [ ] **Step 3: Thread the existing connection through summaries**

Change `site_summary(site, connection=None)` so its two latest-row calls pass
`connection=connection`. Wrap `list_sites_payload()` in one `db_connection()` lease.
Wrap `overview_payload()` similarly and pass the lease to the sites query, changes
query, daily count query, and every `site_summary()` call. Preserve every SQL string,
response field, and ordering rule.

- [ ] **Step 4: Verify GREEN and complete payload compatibility**

Run:

```bash
python3 -m unittest tests.test_database_performance -v
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

### Task 4: Sanitized Slow-Request Diagnostics

**Files:**
- Modify: `tests/test_database_performance.py`
- Modify: `app.py:4250-4290`
- Modify: `app.py:4575-4620`

- [ ] **Step 1: Write failing slow-log sanitization tests**

Add:

```python
    def test_slow_request_log_omits_query_string_and_secrets(self):
        self.assertTrue(hasattr(app, "_slow_request_log_line"), "missing slow log helper")
        line = app._slow_request_log_line(
            "GET",
            "/api/sites/1/perf-metrics?model=secret-token",
            200,
            750.4,
            500,
        )
        self.assertEqual(line, "[慢请求] GET /api/sites/1/perf-metrics 200 750.4ms")
        self.assertNotIn("secret-token", line)
        self.assertIsNone(app._slow_request_log_line("GET", "/api/sites", 200, 99, 500))
        self.assertIsNone(app._slow_request_log_line("GET", "/api/sites", 200, 999, 0))
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_database_performance.DatabasePerformanceTests.test_slow_request_log_omits_query_string_and_secrets -v
```

Expected: FAIL reporting the missing helper.

- [ ] **Step 3: Add the pure helper and handler timing**

Implement:

```python
def _slow_request_log_line(method, target, status, elapsed_ms, threshold_ms):
    if threshold_ms <= 0 or elapsed_ms < threshold_ms:
        return None
    safe_path = urlparse(str(target or "")).path or "/"
    return f"[慢请求] {method or '-'} {safe_path} {int(status or 0)} {elapsed_ms:.1f}ms"
```

In `Handler`, override `send_response()` to record the response status, and wrap
`super().handle_one_request()` with monotonic timing. Print only the helper result,
with `flush=True`, after the request completes. Do not log query strings, headers,
bodies, or response payloads.

- [ ] **Step 4: Verify GREEN**

Run `python3 -m unittest tests.test_database_performance -v` and expect all tests pass.

### Task 5: Document and Pass Runtime Configuration

**Files:**
- Modify: `.env.example:9-40`
- Modify: `docker-compose.yml:40-75`

- [ ] **Step 1: Document safe defaults in `.env.example`**

Add the seven variables from the design with comments explaining that pool size is per
process, `SLOW_REQUEST_THRESHOLD_MS=0` disables logging, and upstream timeout does not
alter the two-second key-read rate limit.

- [ ] **Step 2: Pass settings through Docker Compose**

Under the `upstream.environment` mapping add:

```yaml
DB_POOL_SIZE: ${DB_POOL_SIZE:-8}
DB_POOL_ACQUIRE_TIMEOUT: ${DB_POOL_ACQUIRE_TIMEOUT:-5}
DB_CONNECT_TIMEOUT: ${DB_CONNECT_TIMEOUT:-5}
DB_READ_TIMEOUT: ${DB_READ_TIMEOUT:-15}
DB_WRITE_TIMEOUT: ${DB_WRITE_TIMEOUT:-15}
UPSTREAM_HTTP_TIMEOUT: ${UPSTREAM_HTTP_TIMEOUT:-15}
SLOW_REQUEST_THRESHOLD_MS: ${SLOW_REQUEST_THRESHOLD_MS:-500}
```

- [ ] **Step 3: Validate configuration syntax**

Run:

```bash
docker compose config --quiet
python3 -m py_compile app.py
git diff --check
```

Expected: every command exits zero with no error output.

### Task 6: Full Verification, Benchmark, Review, and Startup

**Files:**
- Verify only.

- [ ] **Step 1: Run all automated checks**

```bash
python3 -m unittest discover -s tests -v
node --experimental-strip-types --test tests/web/automatic-refresh.test.mjs
npm --prefix apps/web run build
python3 -m py_compile app.py
docker compose config --quiet
git diff --check
```

Expected: all Python and Node tests pass, Vite builds successfully, and static/config
checks exit zero.

- [ ] **Step 2: Request an independent code review**

Ask the reviewer to check connection lifecycle, transaction cleanup, pool exhaustion,
shutdown, logging secrecy, unchanged API contracts, and dirty-worktree boundaries.
Fix every confirmed Critical/Important finding and repeat Step 1.

- [ ] **Step 3: Restart the local optimized server**

Stop the existing server on port 8001, then run:

```bash
PORT=8001 python3 app.py
```

Keep the resulting process running for the user.

- [ ] **Step 4: Smoke-test required endpoints**

```bash
for endpoint in /api/overview /api/sites /api/admin/sites /channels; do
  /usr/bin/curl -sS --max-time 15 -o /dev/null \
    -w "$endpoint %{http_code} %{time_total}\n" \
    "http://127.0.0.1:8001$endpoint"
done
```

Expected: each endpoint returns HTTP 200.

- [ ] **Step 5: Repeat the recorded performance workload**

Run the same 8-request sequential and 24-request/12-worker concurrent benchmark used
in the design baseline for `/api/sites`, `/api/overview`, and `/api/admin/sites`.
Report before/after p50, p95, max, and concurrent wall time. Do not benchmark protected
key endpoints in parallel and do not print credentials or response bodies.

- [ ] **Step 6: Deliver the running URL and exact status**

Report `http://127.0.0.1:8001`, verification counts, benchmark deltas, review result,
touched files, and the fact that implementation remains uncommitted because the shared
worktree contains overlapping user changes.
