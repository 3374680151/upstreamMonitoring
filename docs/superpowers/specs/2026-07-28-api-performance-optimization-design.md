# API Performance Optimization Design

## Goal

Reduce intermittent API latency without changing product behavior, page workflows,
API contracts, data freshness, monitoring cadence, or the intentional two-second
NewAPI channel-key request interval.

## Measured Baseline

Local measurements against `http://127.0.0.1:8001` show:

- `GET /api/sites`: p50 299 ms, p95 485 ms for sequential requests.
- `GET /api/overview`: p50 526 ms, with observed requests near 3 seconds.
- 24 concurrent `GET /api/sites` requests: p50 4.5 seconds and 9.1 seconds wall time.
- A new MySQL connection plus one trivial query: p50 24.5 ms.
- 24 trivial queries behind the process-wide database lock: 682 ms wall time.
- The same 24 queries on independent connections: 385 ms wall time.
- An eight-connection warm-pool experiment: 12 ms wall time.
- Main-site channel and group proxy reads currently take about 180 ms when the
  upstream NewAPI instance is healthy.

The database contains only four monitored sites, but `site_summary()` performs two
additional database calls per site. Repeated connection setup and the process-wide
`DB_LOCK` therefore dominate local API latency and create queueing under concurrent
page requests.

## Scope

This change will:

1. Add a bounded, lazy MySQL connection pool using only Python standard-library
   synchronization plus the existing PyMySQL driver.
2. Remove the process-wide lock from ordinary reads and writes. Startup schema
   initialization remains serialized.
3. Allow related read queries to reuse one leased connection, so site-list and
   overview summaries do not reacquire a connection for every site.
4. Make database connection, socket, pool-wait, upstream HTTP, and slow-request
   thresholds configurable through environment variables.
5. Add sanitized slow-request diagnostics that log method, URL path, status, and
   elapsed milliseconds, without query strings, request bodies, credentials, or
   response payloads.
6. Document the new settings in `.env.example` and pass them through Docker Compose.

This change will not:

- cache main-site channels, groups, balances, pricing, or performance data;
- change API response bodies or status-code policy;
- change the frontend polling interval;
- change monitoring schedules or notification behavior;
- reduce the two-second protected channel-key interval or bypass upstream rate limits;
- add a Python dependency beyond PyMySQL;
- migrate, clear, or rewrite existing MySQL data.

## Connection Pool

The pool is a process-local bounded LIFO queue. It starts with empty slots and opens
connections lazily, up to `DB_POOL_SIZE`. Reusing the most recently returned
connection limits idle disconnects and avoids repeated TCP/MySQL handshakes.

Lease behavior:

1. Wait up to `DB_POOL_ACQUIRE_TIMEOUT` for a slot.
2. If the slot has no connection, open one with the configured connect/read/write
   timeouts.
3. If the slot contains an idle connection, call PyMySQL `ping(reconnect=True)`
   before use.
4. On release, roll back any uncommitted transaction before returning the connection.
5. If rollback or connection-health handling fails, close the broken connection and
   return an empty slot so a future lease recreates it.
6. Close all idle connections during normal server shutdown.

Default pool size is eight connections. This remains below the bundled MySQL default
of 151 maximum connections and is enough for the console's concurrent page requests.
Pool exhaustion produces a bounded database-busy error instead of an indefinite wait.

## Database Access

`db_query_all`, `db_query_one`, and `db_execute` lease connections from the pool.
They no longer use the global database lock because every lease owns a distinct
PyMySQL connection and MySQL/InnoDB provides transaction concurrency.

Read helpers accept an optional existing connection. `list_sites_payload()` and
`overview_payload()` lease once and pass that connection through `site_summary()`.
This preserves the current SQL and response shape while eliminating repeated pool
acquisition and keeping each summary read internally consistent.

Startup DDL continues to use its own initialization lock and a dedicated connection.
No schema migration or index change is required for this optimization.

## Runtime Configuration

The following environment variables are added:

| Variable | Default | Purpose |
|---|---:|---|
| `DB_POOL_SIZE` | `8` | Maximum pooled MySQL connections per app process |
| `DB_POOL_ACQUIRE_TIMEOUT` | `5` | Seconds to wait for a pool lease |
| `DB_CONNECT_TIMEOUT` | `5` | MySQL connection timeout in seconds |
| `DB_READ_TIMEOUT` | `15` | MySQL socket read timeout in seconds |
| `DB_WRITE_TIMEOUT` | `15` | MySQL socket write timeout in seconds |
| `UPSTREAM_HTTP_TIMEOUT` | `15` | Existing upstream HTTP timeout, now configurable |
| `SLOW_REQUEST_THRESHOLD_MS` | `500` | Log completed API requests at or above this latency; `0` disables logging |

Invalid numeric values fall back to defaults. Values are clamped to safe positive
ranges so a typo cannot create an unbounded pool or disable required socket handling.

## Slow Request Diagnostics

Each HTTP request records monotonic start time. After completion, requests at or above
the configured threshold emit one line containing only:

- HTTP method;
- parsed URL path without query parameters;
- response status;
- elapsed milliseconds.

Diagnostics never include tokens, cookies, SID values, passwords, 2FA codes, channel
keys, request bodies, query strings, or response content.

## External Upstream Latency

Main-site channel/group endpoints remain real-time pass-through calls. Their latency
cannot be removed locally without caching or changing semantics. The configurable
`UPSTREAM_HTTP_TIMEOUT` bounds failure time, while slow-request diagnostics distinguish
an external proxy delay from local database queueing.

The protected key matching loop remains sequential with its existing two-second
minimum interval. That delay is intentional NewAPI rate-limit protection and is not a
general API performance bug.

## Error Handling

- A failed new connection returns the existing database exception through the current
  request error path; the pool slot remains reusable.
- A broken leased connection is discarded rather than returned to the idle pool.
- A pool lease timeout fails in bounded time with a sanitized database-busy message.
- Query and commit failures roll back before releasing the connection.
- Existing upstream HTTP errors and API response contracts remain unchanged.

## Testing

Automated tests will cover:

- concurrent database reads can overlap instead of being serialized by `DB_LOCK`;
- sequential requests reuse an idle pooled connection;
- the pool never leases more than its configured size;
- broken connections are discarded and recreated;
- pool acquisition times out cleanly;
- a leased connection is rolled back before reuse;
- related site-summary queries reuse the supplied connection and preserve payloads;
- slow-request logs omit query strings and secrets;
- environment parsing uses defaults and clamps invalid values.

Verification will include the existing Python and frontend tests, TypeScript/Vite
build, `py_compile`, `git diff --check`, local API smoke tests, and before/after latency
benchmarks using the same sequential and concurrent workloads from the baseline.

## Success Criteria

- No API contract or UI workflow changes.
- Existing tests remain green and new pool/diagnostic tests pass.
- Local `/api/sites` and `/api/overview` latency materially improve from the recorded
  baseline.
- Concurrent local API requests no longer exhibit multi-second queueing caused by the
  process-wide database lock.
- Real-time upstream reads and protected-key rate limiting remain intact.
