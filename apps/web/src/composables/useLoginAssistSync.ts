/**
 * 登录态同步 + 登录引导。
 *
 * 点击同步后若上游没有登录态（no_session），不再只提示「请提前登录」：
 * 1. 让扩展前台打开站点首页（旧扩展回退 window.open，最终兜底为弹窗内手动链接）；
 * 2. 弹窗进入等待状态，期间每 6 秒自动重跑一次 session-sync；
 * 3. 用户在站点页完成登录后，下一次探测即可拿到登录态并由后端落库保留；
 * 4. 权限缺失 / 扩展断开时提前结束，超时（默认 5 分钟）或取消时放弃。
 */
import { reactive } from "vue";
import { openSiteLoginTab, syncSiteBrowserSession } from "@/lib/browserSessionBridge";
import type { Site, SiteSessionSyncState } from "@/lib/types";

const POLL_INTERVAL_MS = 6000;
const WAIT_TIMEOUT_MS = 5 * 60 * 1000;

export type LoginAssistPhase =
  | "probing"
  | "opening"
  | "waiting"
  | "success"
  | "stopped";

export type LoginAssistSite = Pick<Site, "id" | "name" | "base_url">;

export const loginAssistState = reactive({
  open: false,
  siteName: "",
  siteUrl: "",
  phase: "probing" as LoginAssistPhase,
  attempt: 0,
  hint: "",
});

let runToken = 0;
let wake: (() => void) | null = null;

function waitForNextProbe(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(finish, milliseconds);
    function finish() {
      wake = null;
      window.clearTimeout(timer);
      resolve();
    }
    wake = finish;
  });
}

/** 立即触发下一次登录态探测（弹窗「立即重试」按钮）。 */
export function retryLoginAssistNow(): void {
  wake?.();
}

/** 关闭弹窗并放弃本次登录引导（不影响已完成的同步）。 */
export function cancelLoginAssist(): void {
  runToken += 1;
  wake?.();
  loginAssistState.open = false;
}

function stoppedState(
  site: LoginAssistSite,
  message: string,
): SiteSessionSyncState {
  return {
    request_id: "",
    target_kind: "site",
    platform: "sub2api",
    target_origin: site.base_url,
    status: "no_session",
    error_code: "LOGIN_ASSIST_STOPPED",
    message,
  };
}

async function openSitePage(site: LoginAssistSite): Promise<void> {
  loginAssistState.phase = "opening";
  const openedByExtension = await openSiteLoginTab(site.base_url);
  if (openedByExtension) return;
  // 旧版本扩展：回退 window.open；被弹窗拦截时 result 为 null，
  // 弹窗内的站点链接是最终兜底（真实用户手势，不会被拦截）。
  try {
    window.open(site.base_url, "_blank", "noopener");
  } catch {
    // 拦截时依赖弹窗内手动链接
  }
}

export async function syncWithLoginAssist(
  site: LoginAssistSite,
): Promise<SiteSessionSyncState> {
  const token = ++runToken;
  loginAssistState.open = true;
  loginAssistState.siteName = site.name;
  loginAssistState.siteUrl = site.base_url;
  loginAssistState.attempt = 1;
  loginAssistState.hint = "";
  loginAssistState.phase = "probing";

  const alive = () => token === runToken;
  const first = await syncSiteBrowserSession(site.id).catch(
    (err: unknown) =>
      stoppedState(site, err instanceof Error ? err.message : String(err)),
  );
  if (!alive()) return stoppedState(site, "已取消");
  if (first.status === "ready") {
    loginAssistState.phase = "success";
    loginAssistState.open = false;
    return first;
  }
  if (first.status !== "no_session") {
    // 扩展未连接 / 权限缺失等：等登录也解决不了，交给调用方按原逻辑提示
    loginAssistState.open = false;
    return first;
  }

  await openSitePage(site);
  if (!alive()) return stoppedState(site, "已取消");
  loginAssistState.hint = "未检测到登录态，请在打开的站点页面完成登录";

  const deadline = Date.now() + WAIT_TIMEOUT_MS;
  let last = first;
  while (Date.now() < deadline) {
    loginAssistState.phase = "waiting";
    await waitForNextProbe(POLL_INTERVAL_MS);
    if (!alive()) return stoppedState(site, "已取消");
    loginAssistState.attempt += 1;
    loginAssistState.phase = "probing";
    last = await syncSiteBrowserSession(site.id).catch((err: unknown) =>
      stoppedState(site, err instanceof Error ? err.message : String(err)),
    );
    if (!alive()) return stoppedState(site, "已取消");
    if (last.status === "ready") {
      loginAssistState.phase = "success";
      loginAssistState.open = false;
      return last;
    }
    if (
      last.status === "permission_required" ||
      last.status === "extension_unavailable"
    ) {
      // 这两类失败与是否登录无关，继续等待没有意义
      loginAssistState.phase = "stopped";
      loginAssistState.hint = last.message || last.error_code || "同步无法继续";
      return last;
    }
    if (last.status === "failed" && last.message) {
      loginAssistState.hint = last.message;
    }
  }
  loginAssistState.phase = "stopped";
  loginAssistState.hint = "等待登录超时，请登录后重新点击同步";
  return stoppedState(site, "等待登录超时，请登录后重新点击同步");
}
