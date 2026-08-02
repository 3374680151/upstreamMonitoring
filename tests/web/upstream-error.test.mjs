import assert from "node:assert/strict";
import test from "node:test";

const moduleUrl = new URL(
  "../../apps/web/src/lib/upstreamError.ts",
  import.meta.url,
);
const errorModule = await import(moduleUrl).catch((error) => {
  if (error?.code === "ERR_MODULE_NOT_FOUND") return null;
  throw error;
});

test("explains common upstream errors while retaining raw text", () => {
  assert.notEqual(errorModule, null, "upstreamError.ts is missing");
  assert.deepEqual(
    errorModule.explainUpstreamError(
      "<urlopen error [Errno 54] Connection reset by peer>",
    ),
    {
      summary: "上游主动重置连接",
      raw: "<urlopen error [Errno 54] Connection reset by peer>",
    },
  );
  assert.equal(
    errorModule.explainUpstreamError("timed out").summary,
    "连接上游超时",
  );
  assert.equal(
    errorModule.explainUpstreamError("Name or service not known").summary,
    "无法解析上游域名",
  );
  assert.equal(
    errorModule.explainUpstreamError("HTTP 403").summary,
    "上游拒绝访问（HTTP 403）",
  );
  assert.equal(
    errorModule.explainUpstreamError("HTTP 429").summary,
    "上游触发请求限流（HTTP 429）",
  );
  assert.deepEqual(errorModule.explainUpstreamError("socket closed"), {
    summary: "上游请求失败",
    raw: "socket closed",
  });
});
