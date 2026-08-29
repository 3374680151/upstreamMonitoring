/**
 * 登录态同步 + 登录引导（完全手动驱动）。
 *
 * 点击行内「同步」后弹窗接管：
 * 1. 第一次探测是用户点「同步」触发的；若没有登录态（no_session），
 *    让扩展前台打开站点首页（旧扩展回退 window.open，兜底为弹窗内手动链接），
 *    弹窗进入等待，不做任何自动重试；
 * 2. 用户在站点页登录完成后，回本页点「我已登录完成」才再探测一次；
 *    成功则落库保留并关闭弹窗，仍无登录态则提示后再等下一次手动触发；
 * 3. 权限缺失 / 扩展断开 / 其他失败：弹窗保持打开，显示原因与修复指引，
 *    由用户处理后手动点「重试」；
 * 4. 取消弹窗随时可用，不影响已完成的同步。
 */
import { reactive } from "vue";
import {
  extensionRequiredMessage,
  openSiteLoginTab,
  syncSiteBrowserSession,
} from "@/lib/browserSessionBridge";
import type { Site, SiteSessionSyncState } from "@/lib/types";

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
let currentSite: LoginAssistSite | null = null;
let openedLoginPage = false;
let settledNotifier:
  | ((result: SiteSessionSyncState, site: LoginAssistSite | null) => void)
  | null = null;

/** 注册弹窗关闭（成功 / 取消）时的回调：刷新列表并给出最终提示。 */
export function onLoginAssistSettled(
  callback: (result: SiteSessionSyncState, site: LoginAssistSite | null) => void,
): void {
  settledNotifier = callback;
}

function isBusy(): boolean {
  return (
    loginAssistState.phase === "probing" ||
    loginAssistState.phase === "opening"
  );
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

function failureGuidance(state: SiteSessionSyncState): string {
  if (state.status === "extension_unavailable") {
    return extensionRequiredMessage();
  }
  if (state.status === "permission_required") {
    const base = state.message ? `${state.message}。` : "";
    return `${base}修复方式：打开 chrome://extensions →「Upstream 登录态同步」→ 详情，将「网站访问权限」设为「在所有网站上」，然后点「重试」。也可以先手动登录站点。`;
  }
  return state.message || state.error_code || "登录态同步失败";
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

/** 执行一次同步探测；调用方保证不在 busy 状态下重入。 */
async function runAttempt(token: number): Promise<SiteSessionSyncState | null> {
  const site = currentSite;
  if (!site || token !== runToken) return null;
  loginAssistState.attempt += 1;
  loginAssistState.phase = "probing";
  loginAssistState.hint = "";
  const result = await syncSiteBrowserSession(site.id).catch((err: unknown) =>
    stoppedState(site, err instanceof Error ? err.message : String(err)),
  );
  if (token !== runToken) return null;
  if (result.status === "ready") {
    loginAssistState.phase = "success";
    loginAssistState.open = false;
    const notifier = settledNotifier;
    currentSite = null;
    notifier?.(result, site);
    return result;
  }
  if (result.status === "no_session") {
    if (!openedLoginPage) {
      openedLoginPage = true;
      await openSitePage(site);
      if (token !== runToken) return null;
      loginAssistState.hint = "未检测到登录态，请在打开的站点页面完成登录";
    } else {
      loginAssistState.hint =
        "仍未检测到登录态，请确认已在站点页登录成功，再点「我已登录完成」";
    }
    loginAssistState.phase = "waiting";
  } else {
    // 权限缺失 / 扩展断开 / 其他失败：保持弹窗并给出处理指引
    loginAssistState.phase = "stopped";
    loginAssistState.hint = failureGuidance(result);
  }
  return result;
}

/** 行内「同步」入口：打开弹窗并执行第一次探测（这次由用户的点击触发）。 */
export async function syncWithLoginAssist(
  site: LoginAssistSite,
): Promise<SiteSessionSyncState> {
  const token = ++runToken;
  currentSite = site;
  openedLoginPage = false;
  loginAssistState.open = true;
  loginAssistState.siteName = site.name;
  loginAssistState.siteUrl = site.base_url;
  loginAssistState.attempt = 0;
  loginAssistState.hint = "";
  loginAssistState.phase = "probing";
  const result = await runAttempt(token);
  return result ?? stoppedState(site, "已取消");
}

/** 弹窗「我已登录完成 / 重试」按钮：手动触发下一次探测，不自动轮询。 */
export function retryLoginAssistNow(): void {
  if (!loginAssistState.open || !currentSite || isBusy()) return;
  void runAttempt(runToken);
}

/** 关闭弹窗并放弃本次登录引导（不影响已完成的同步）。 */
export function cancelLoginAssist(): void {
  const hadSession = loginAssistState.open;
  runToken += 1;
  loginAssistState.open = false;
  const notifier = settledNotifier;
  const site = currentSite;
  currentSite = null;
  if (hadSession && site) {
    notifier?.(stoppedState(site, "已取消"), site);
  }
}
