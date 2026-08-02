# Channel Create and sub2api Hardening Implementation Plan

**Goal:** Make channel creation cache submitted keys even when NewAPI omits the created ID, and harden sub2api pagination and token refresh without regressing existing matching behavior.

**Architecture:** Keep channel-ID resolution and key-cache persistence in the backend. Add a per-site sub2api refresh lock and make pagination report truncation instead of silently returning partial data. Preserve the existing API shapes unless a new non-secret diagnostic field is required.

**Tech Stack:** Python stdlib HTTP client, PyMySQL, unittest, React/Vite build.

### Task 1: Reproduce the missing-ID cache regression

**Files:**
- Modify: `tests/test_newapi_channel_key_matching.py`
- Modify: `app.py:5334-5410`

- [x] Add a failing test where the upstream create response is successful but has no ID, while a follow-up channel list contains exactly one new matching channel; assert the handler returns the resolved ID and persists the submitted plaintext key.
- [x] Run the focused test and confirm it fails because the current handler returns no ID and does not persist the key.
- [x] Add a backend resolver that snapshots existing IDs, creates the channel, and resolves the new ID from a unique name/base URL candidate when the upstream response omits it.
- [x] Run the focused test and confirm it passes.

### Task 2: Harden sub2api pagination and refresh concurrency

**Files:**
- Modify: `tests/test_sub2api_channel_key_matching.py`
- Modify: `app.py:3253-3291,3335-3491`

- [x] Add a failing test proving a full `max_pages` response without total/pages metadata is reported as truncated rather than silently successful.
- [x] Add a failing test proving concurrent refreshes for one sub2api site serialize through one lock.
- [x] Run the focused tests and confirm they fail for the current implementation.
- [x] Implement explicit truncation metadata/error and a per-site refresh lock shared by groups, models, and account flows.
- [x] Run all sub2api tests and confirm they pass.

### Task 3: Regression verification

**Files:**
- No production changes.

- [x] Run all Python tests.
- [x] Run all web tests and the production build.
- [x] Use a temporary uniquely named channel against the configured NewAPI main site to verify create, cache, and delete; never reuse or delete GoPay without a recoverable key.
- [x] Verify the three configured sub2api sites through groups, model data, and account endpoints without printing credentials.
- [x] Run `git diff --check` and inspect the final diff for secret exposure.
