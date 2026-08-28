const VERSION = "upstream-session-bridge/v2";
const PAGE_SOURCE = "upstream-console";
const EXTENSION_SOURCE = "upstream-session-bridge";

function post(type, correlationId, result = {}) {
  window.postMessage(
    {
      source: EXTENSION_SOURCE,
      version: VERSION,
      type,
      correlation_id: String(correlationId || ""),
      result,
    },
    window.location.origin,
  );
}

function extensionUnavailableResult() {
  return {
    ok: false,
    status: "extension_unavailable",
    code: "EXTENSION_UNAVAILABLE",
    message: "扩展已更新或未连接，请刷新当前 Upstream 页面后重试",
  };
}

async function forwardRuntimeMessage(message, correlationId, responseType) {
  try {
    const result = await chrome.runtime.sendMessage(message);
    if (!result || typeof result !== "object") {
      post(responseType, correlationId, extensionUnavailableResult());
      return;
    }
    post(responseType, correlationId, {
      ok: result.ok === true,
      status: String(result.status || "failed"),
      code: String(result.code || ""),
      message: String(result.message || ""),
    });
  } catch {
    post(responseType, correlationId, extensionUnavailableResult());
  }
}

window.addEventListener("message", (event) => {
  if (
    event.source !== window ||
    event.origin !== window.location.origin ||
    event.data?.source !== PAGE_SOURCE ||
    event.data?.version !== VERSION
  ) {
    return;
  }
  const correlationId = String(event.data.correlation_id || "");
  if (!correlationId) return;
  if (event.data.type === "UPSTREAM_SESSION_BRIDGE_PROBE") {
    forwardRuntimeMessage(
      {
        version: VERSION,
        type: "UPSTREAM_SESSION_BRIDGE_PROBE",
      },
      correlationId,
      "UPSTREAM_SESSION_BRIDGE_ACK",
    );
    return;
  }
  if (event.data.type === "UPSTREAM_SESSION_BRIDGE_OPEN_TAB") {
    forwardRuntimeMessage(
      {
        version: VERSION,
        type: "UPSTREAM_SESSION_BRIDGE_OPEN_TAB",
        request: { target_origin: event.data.request?.target_origin || "" },
      },
      correlationId,
      "UPSTREAM_SESSION_BRIDGE_TAB_ACK",
    );
    return;
  }
  if (event.data.type !== "UPSTREAM_SESSION_BRIDGE_START") return;
  forwardRuntimeMessage(
    {
      version: VERSION,
      type: "UPSTREAM_SESSION_BRIDGE_START",
      request: event.data.request,
    },
    correlationId,
    "UPSTREAM_SESSION_BRIDGE_RESULT",
  );
});
