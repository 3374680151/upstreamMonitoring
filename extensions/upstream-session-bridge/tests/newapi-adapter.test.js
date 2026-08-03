import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import {
  newApiCompletionPayload,
  normalizeNewApiInMemoryAuth,
  normalizeNewApiRefreshBundle,
  readNewApiLegacySessionValues,
  selectNewApiBrowserCookie,
  selectNewApiRefreshCookie,
} from "../adapters/newapi.js";

test("classifies target-access failures without exposing browser error text", async () => {
  const { classifyExtensionSyncFailure } = await import(
    "../adapters/sync-errors.js"
  );

  const result = classifyExtensionSyncFailure(
    new Error(
      "Cannot access contents of url https://aiinfinite.online/. Extension manifest must request permission",
    ),
  );

  assert.deepEqual(result, {
    status: "permission_required",
    code: "ORIGIN_PERMISSION_REQUIRED",
    message: "扩展需要该站点的读取权限",
  });
  assert.doesNotMatch(JSON.stringify(result), /aiinfinite|manifest|contents/i);
});

test("reads only allowlisted legacy NewAPI storage keys", () => {
  const reads = [];
  const values = new Map([
    ["user", JSON.stringify({ id: 21, access_token: "legacy-access" })],
    ["access_token", ""],
    ["token", ""],
    ["user_id", ""],
    ["password", "must-not-read"],
    ["refresh_token", "must-not-read"],
  ]);

  const session = readNewApiLegacySessionValues((key) => {
    reads.push(key);
    return values.get(key);
  });

  assert.deepEqual(session, {
    access_token: "legacy-access",
    access_user_id: "21",
  });
  assert.deepEqual(reads, ["user", "access_token", "token", "user_id", "uid"]);
});

test("reads the uid-only storage shape used by cookie-authenticated NewAPI builds", () => {
  const reads = [];
  const values = new Map([
    ["user", JSON.stringify({ username: "demo" })],
    ["access_token", ""],
    ["token", ""],
    ["user_id", ""],
    ["uid", "21"],
  ]);
  assert.deepEqual(
    readNewApiLegacySessionValues((key) => {
      reads.push(key);
      return values.get(key);
    }),
    { access_user_id: "21" },
  );
  assert.deepEqual(reads, ["user", "access_token", "token", "user_id", "uid"]);
});

test("builds a deterministic cookie header without cookie metadata", () => {
  assert.equal(
    selectNewApiBrowserCookie([
      { name: "sid", value: "session-value", httpOnly: true },
      { name: "uid", value: "21" },
      { name: "sid", value: "replaced" },
      { name: "bad name", value: "ignored" },
    ]),
    "sid=replaced; uid=21",
  );
});

test("extracts legacy user id from explicit separate keys", () => {
  const values = new Map([
    ["user", JSON.stringify({ username: "demo" })],
    ["access_token", "separate-access"],
    ["token", ""],
    ["user_id", "42"],
  ]);
  assert.deepEqual(
    readNewApiLegacySessionValues((key) => values.get(key)),
    { access_token: "separate-access", access_user_id: "42" },
  );
});

test("rejects incomplete and mixed legacy NewAPI storage shapes", () => {
  const onlyToken = new Map([
    ["user", "{}"],
    ["access_token", "access"],
    ["token", ""],
    ["user_id", ""],
  ]);
  assert.equal(
    readNewApiLegacySessionValues((key) => onlyToken.get(key)),
    null,
  );

  const malformedUser = new Map([
    ["user", "{broken"],
    ["access_token", ""],
    ["token", "fallback-token"],
    ["user_id", ""],
  ]);
  assert.equal(
    readNewApiLegacySessionValues((key) => malformedUser.get(key)),
    null,
  );
});

test("normalizes a complete modern NewAPI refresh bundle", () => {
  const bundle = {
    access_token: "modern-access",
    access_expires_at: 4102444800,
    user: { id: 21 },
    session: { sid: "session-21" },
  };
  assert.deepEqual(
    normalizeNewApiRefreshBundle(bundle, "rotating-refresh"),
    {
      access_token: "modern-access",
      access_user_id: "21",
      browser_session_id: "session-21",
      browser_refresh_cookie: "new_api_refresh=rotating-refresh",
      browser_access_expires_at: 4102444800,
    },
  );
});

test("normalizes the current in-memory NewAPI authentication state", () => {
  assert.deepEqual(
    normalizeNewApiInMemoryAuth({
      accessToken: "modern-access",
      accessExpiresAt: 4102444800,
      user: { id: 21, username: "admin" },
      session: { sid: "session-21", current: true },
    }),
    {
      access_token: "modern-access",
      access_user_id: "21",
      browser_session_id: "session-21",
      browser_access_expires_at: 4102444800,
    },
  );
  assert.equal(
    normalizeNewApiInMemoryAuth({
      accessToken: "modern-access",
      accessExpiresAt: 4102444800,
      user: { id: 21 },
      session: {},
    }),
    null,
  );
});

test("rejects partial or mixed-version modern refresh bundles", () => {
  const base = {
    access_token: "modern-access",
    access_expires_at: 4102444800,
    user: { id: 21 },
    session: { sid: "session-21" },
  };
  assert.equal(
    normalizeNewApiRefreshBundle({ ...base, session: {} }, "refresh"),
    null,
  );
  assert.equal(normalizeNewApiRefreshBundle(base, ""), null);
  assert.equal(
    normalizeNewApiRefreshBundle(
      { access_token: "legacy", user_id: 21, session_id: "mixed" },
      "refresh",
    ),
    null,
  );
});

test("selects only the exact NewAPI refresh cookie name", () => {
  assert.equal(
    selectNewApiRefreshCookie([
      { name: "session", value: "other" },
      { name: "new_api_refresh_backup", value: "wrong" },
      { name: "new_api_refresh", value: "right" },
    ]),
    "right",
  );
  assert.equal(
    selectNewApiRefreshCookie([{ name: "NEW_API_REFRESH", value: "wrong" }]),
    "",
  );
});

test("builds redacted NewAPI completion envelopes", () => {
  const session = {
    access_token: "modern-access",
    access_user_id: "21",
  };
  const found = newApiCompletionPayload("https://newapi.example/path", session);
  assert.equal(found.status, "session_found");
  assert.equal(found.platform, "newapi");
  assert.equal(found.observed_origin, "https://newapi.example");
  assert.equal(found.session, session);
  assert.deepEqual(newApiCompletionPayload("https://newapi.example", null), {
    status: "no_session",
    platform: "newapi",
    observed_origin: "https://newapi.example",
  });
});

test("browser sync does not depend on runtime permission prompts", async () => {
  const testDir = new URL(".", import.meta.url);
  const manifest = JSON.parse(
    await readFile(
      fileURLToPath(new URL("../manifest.json", testDir)),
      "utf8",
    ),
  );
  assert.equal(manifest.permissions.includes("cookies"), true);
  assert.equal(manifest.host_permissions.includes("http://*/*"), true);
  assert.equal(manifest.host_permissions.includes("https://*/*"), true);
  assert.equal(manifest.optional_permissions, undefined);
  assert.equal(manifest.optional_host_permissions, undefined);

  const worker = await readFile(
    fileURLToPath(new URL("../service-worker.js", testDir)),
    "utf8",
  );
  assert.doesNotMatch(worker, /chrome\.permissions\.request/);
});

test("admin targets require modern NewAPI browser session state", async () => {
  const testDir = new URL(".", import.meta.url);
  const worker = await readFile(
    fileURLToPath(new URL("../service-worker.js", testDir)),
    "utf8",
  );
  assert.match(worker, /new Set\(\["site", "admin_site"\]\)/);
  assert.match(worker, /targetKind === "site"/);
  assert.match(
    worker,
    /readNewApiTargetSession\(\s*tabId,\s*targetOrigin,\s*targetKind,?/s,
  );
});

test("worker reads active in-memory NewAPI auth before using the refresh endpoint", async () => {
  const testDir = new URL(".", import.meta.url);
  const worker = await readFile(
    fileURLToPath(new URL("../service-worker.js", testDir)),
    "utf8",
  );
  assert.match(worker, /readNewApiInMemorySessionInPage/);
  assert.match(worker, /webpackChunknew_api/);
  assert.match(worker, /normalizeNewApiInMemoryAuth/);
  assert.ok(
    worker.indexOf("readNewApiInMemorySessionInPage") <
      worker.indexOf("refreshNewApiSessionInPage"),
  );
});
