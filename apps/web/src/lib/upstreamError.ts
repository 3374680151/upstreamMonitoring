export type UpstreamErrorExplanation = {
  summary: string;
  raw: string;
};

export function explainUpstreamError(value: unknown): UpstreamErrorExplanation {
  const raw = String(value || "未知错误").trim() || "未知错误";
  const lower = raw.toLowerCase();

  if (
    lower.includes("errno 54") ||
    lower.includes("connection reset by peer") ||
    lower.includes("econnreset")
  ) {
    return { summary: "上游主动重置连接", raw };
  }
  if (lower.includes("timed out") || lower.includes("timeout")) {
    return { summary: "连接上游超时", raw };
  }
  if (
    lower.includes("name or service not known") ||
    lower.includes("temporary failure in name resolution") ||
    lower.includes("nodename nor servname")
  ) {
    return { summary: "无法解析上游域名", raw };
  }
  if (/http\s*403\b/i.test(raw)) {
    return { summary: "上游拒绝访问（HTTP 403）", raw };
  }
  if (/http\s*401\b/i.test(raw)) {
    return { summary: "上游认证失败（HTTP 401）", raw };
  }
  if (/http\s*429\b/i.test(raw)) {
    return { summary: "上游触发请求限流（HTTP 429）", raw };
  }
  return { summary: "上游请求失败", raw };
}
