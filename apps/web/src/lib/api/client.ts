/**
 * HTTP client primitives shared by every resource-domain module.
 *
 * 对应后端 `backend/core/security.py` 的 Bearer 鉴权约定与
 * `backend/main.py` 的统一异常处理：401 → 清 token 并广播全局事件，
 * 让 useAuth 把会话切回登录页。
 */

const TOKEN_KEY = "console_token";

export function getConsoleToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setConsoleToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore storage errors (private mode etc.) */
  }
}

/**
 * 统一 fetch 封装：注入 Bearer token、解析 JSON、处理 401 与非 JSON 兜底。
 * 各资源域模块只关心 path/options，不重复处理鉴权与错误形态。
 */
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getConsoleToken();
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
    ...options,
  });
  const text = await res.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { raw: text };
  }
  if (
    res.ok &&
    body &&
    typeof body === "object" &&
    typeof body.raw === "string"
  ) {
    throw new Error("接口返回了网页内容而不是 JSON，请重启后端服务");
  }
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    // 会话失效：清 token 并通知全局切回登录页
    setConsoleToken("");
    window.dispatchEvent(new CustomEvent("console-unauthorized"));
  }
  if (!res.ok) {
    throw new Error(body?.message || body?.error || `HTTP ${res.status}`);
  }
  return body as T;
}
