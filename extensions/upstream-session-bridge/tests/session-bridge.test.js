import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import {
  completionPayload,
  isLoopbackCompletionUrl,
  normalizeOrigin,
  readSub2ApiSessionValues,
  tokenFreePageResult,
} from "../adapters/sub2api.js";
import { selectExistingTargetTab } from "../adapters/target-tab.js";
import { classifyExtensionSyncFailure } from "../adapters/sync-errors.js";

test("selects only an already-open tab with the requested origin", () => {
  assert.deepEqual(
    selectExistingTargetTab(
      [
        { id: 3, url: "https://other.example/" },
        { id: 7, url: "https://aiinfinite.online/usage-logs/common" },
      ],
      "https://aiinfinite.online",
    ),
    { id: 7, url: "https://aiinfinite.online/usage-logs/common" },
  );
  assert.equal(
    selectExistingTargetTab(
      [{ id: 8, url: "https://other.example/" }],
      "https://aiinfinite.online",
    ),
    null,
  );
});

test("reads only the three sub2api session keys", () => {
  const reads = [];
  const values = new Map([
    ["auth_token", "at"],
    ["refresh_token", "rt"],
    ["token_expires_at", "1785340000000"],
    ["password", "must-not-read"],
  ]);
  const session = readSub2ApiSessionValues((key) => {
    reads.push(key);
    return values.get(key);
  });
  assert.deepEqual(session, {
    access_token: "at",
    refresh_token: "rt",
    token_expires_at: "1785340000000",
  });
  assert.deepEqual(reads, [
    "auth_token",
    "refresh_token",
    "token_expires_at",
  ]);
});

test("requires both access and refresh token for a sub2api session", () => {
  assert.equal(
    readSub2ApiSessionValues((key) =>
      key === "auth_token" ? "at" : "",
    ),
    null,
  );
});

test("normalizes exact HTTP origins and rejects credentials", () => {
  assert.equal(
    normalizeOrigin("https://Example.com:8443/path?q=1"),
    "https://example.com:8443",
  );
  assert.equal(normalizeOrigin("https://user:pass@example.com"), "");
  assert.equal(normalizeOrigin("javascript:alert(1)"), "");
  assert.equal(normalizeOrigin("https://[broken"), "");
});

test("builds session-found and no-session completion payloads", () => {
  const found = completionPayload("https://example.com", {
    access_token: "at",
    refresh_token: "rt",
    token_expires_at: "1",
  });
  assert.equal(found.status, "session_found");
  assert.equal(found.platform, "sub2api");
  assert.equal(found.session.access_token, "at");

  assert.deepEqual(completionPayload("https://example.com", null), {
    status: "no_session",
    platform: "sub2api",
    observed_origin: "https://example.com",
  });
});

test("allows only loopback completion endpoints without query or hash", () => {
  assert.equal(
    isLoopbackCompletionUrl(
      "http://127.0.0.1:8000/api/session-sync/requests/abc_123/complete",
    ),
    true,
  );
  assert.equal(
    isLoopbackCompletionUrl(
      "http://localhost:8000/api/session-sync/requests/abc-123/complete",
    ),
    true,
  );
  assert.equal(
    isLoopbackCompletionUrl(
      "https://upstream.example/api/session-sync/requests/abc/complete",
    ),
    false,
  );
  assert.equal(
    isLoopbackCompletionUrl(
      "http://127.0.0.1:8000/api/session-sync/requests/abc/complete?secret=x",
    ),
    false,
  );
});

test("page results never contain session values", () => {
  assert.deepEqual(
    tokenFreePageResult({
      success: false,
      status: "no_session",
      code: "NO_SESSION",
      message: "没有登录态，请提前登录",
      access_token: "must-not-leak",
      refresh_token: "must-not-leak",
    }),
    {
      ok: false,
      status: "no_session",
      code: "NO_SESSION",
      message: "没有登录态，请提前登录",
    },
  );
});

test("service worker does not persist browser credentials", async () => {
  const testDir = new URL(".", import.meta.url);
  const source = await readFile(
    fileURLToPath(new URL("../service-worker.js", testDir)),
    "utf8",
  );
  assert.doesNotMatch(source, /chrome\.storage\.(?:local|sync)\.set/);
  assert.doesNotMatch(source, /sendResponse\([^)]*(?:access_token|refresh_token)/s);
});

test("service worker opens a background tab only when needed and always cleans it up", async () => {
  const testDir = new URL(".", import.meta.url);
  const source = await readFile(
    fileURLToPath(new URL("../service-worker.js", testDir)),
    "utf8",
  );
  assert.match(source, /chrome\.tabs\.create\(\{[\s\S]*?active:\s*false/);
  assert.match(source, /chrome\.tabs\.remove\(createdTabId\)/);
  assert.doesNotMatch(source, /active:\s*true/);
  assert.match(source, /newApiCompletionPayload\(request\.targetOrigin, null\)/);
  assert.match(source, /sub2ApiCompletionPayload\(request\.targetOrigin, null\)/);
});

test("unknown extension failures expose only a safe diagnostic stage", () => {
  const result = classifyExtensionSyncFailure(
    new Error("secret refresh token must never leave the extension"),
    "cookie_read",
  );

  assert.deepEqual(result, {
    status: "failed",
    code: "SYNC_FAILED",
    message: "登录态同步失败（阶段：Cookie 读取）",
  });
  assert.doesNotMatch(JSON.stringify(result), /secret|refresh token/i);
});

test("unknown diagnostic stages fall back to a redacted generic failure", () => {
  assert.deepEqual(
    classifyExtensionSyncFailure(new Error("private detail"), "private-stage"),
    {
      status: "failed",
      code: "SYNC_FAILED",
      message: "登录态同步失败",
    },
  );
});

test("bridge probe verifies the runtime instead of acknowledging locally", async () => {
  const testDir = new URL(".", import.meta.url);
  const [contentScript, worker] = await Promise.all([
    readFile(fileURLToPath(new URL("../content-script.js", testDir)), "utf8"),
    readFile(fileURLToPath(new URL("../service-worker.js", testDir)), "utf8"),
  ]);

  assert.match(
    contentScript,
    /UPSTREAM_SESSION_BRIDGE_PROBE[\s\S]*?forwardRuntimeMessage/,
  );
  assert.match(contentScript, /function forwardRuntimeMessage[\s\S]*?chrome\.runtime\.sendMessage/);
  assert.doesNotMatch(
    contentScript,
    /UPSTREAM_SESSION_BRIDGE_PROBE[\s\S]{0,220}?post\("UPSTREAM_SESSION_BRIDGE_ACK"/,
  );
  assert.match(worker, /message\?\.type === "UPSTREAM_SESSION_BRIDGE_PROBE"/);
});

test("content script catches an invalidated extension context synchronously", async () => {
  const testDir = new URL(".", import.meta.url);
  const contentScript = await readFile(
    fileURLToPath(new URL("../content-script.js", testDir)),
    "utf8",
  );

  assert.match(
    contentScript,
    /try\s*\{[\s\S]*?chrome\.runtime\.sendMessage[\s\S]*?\}\s*catch\s*\{/,
  );
  assert.match(contentScript, /EXTENSION_UNAVAILABLE/);
});

test("content script uses promise messaging so invalidated callbacks cannot escape", async () => {
  const testDir = new URL(".", import.meta.url);
  const contentScript = await readFile(
    fileURLToPath(new URL("../content-script.js", testDir)),
    "utf8",
  );

  assert.match(contentScript, /await chrome\.runtime\.sendMessage\(message\)/);
  assert.doesNotMatch(
    contentScript,
    /chrome\.runtime\.sendMessage\(message,\s*\(result\)\s*=>/,
  );
});

test("extension popup can reload an updated unpacked extension", async () => {
  const testDir = new URL(".", import.meta.url);
  const [popupHtml, popupScript] = await Promise.all([
    readFile(fileURLToPath(new URL("../popup.html", testDir)), "utf8"),
    readFile(fileURLToPath(new URL("../popup.js", testDir)), "utf8"),
  ]);

  assert.match(popupHtml, /id="reload-extension"/);
  assert.match(popupScript, /chrome\.runtime\.reload\(\)/);
});
