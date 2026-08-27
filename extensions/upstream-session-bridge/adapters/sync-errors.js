function errorText(error) {
  return String(error?.message || error || "").toLowerCase();
}

const DIAGNOSTIC_STAGE_LABELS = Object.freeze({
  origin_permission: "站点权限",
  target_tab_query: "目标标签页查询",
  target_tab_open: "目标标签页打开",
  page_storage_read: "页面登录态读取",
  page_memory_read: "页面内存登录态读取",
  cookie_permission: "Cookie 权限",
  cookie_read: "Cookie 读取",
  refresh_fallback: "登录态刷新",
  backend_completion: "本地后端回传",
});

export function classifyExtensionSyncFailure(error, diagnosticStage = "") {
  const message = errorText(error);
  // Chrome 错误页（站点打不开时后台标签页停在 chrome-error://chromewebdata）
  // 会被 executeScript 报成「无法访问内容」，并非权限问题，须先于权限规则判定
  if (/chrome-error/.test(message)) {
    return {
      status: "failed",
      code: "SITE_UNREACHABLE",
      message:
        "站点无法访问：浏览器打开该站点失败（网络错误或站点宕机），请先在浏览器中手动打开确认",
    };
  }
  if (/cookie|new_api_refresh/.test(message)) {
    return {
      status: "permission_required",
      code: "COOKIE_PERMISSION_REQUIRED",
      message: "扩展需要读取 NewAPI 登录 Cookie 的权限",
    };
  }
  if (
    /permission|cannot access contents|cannot access a chrome|scripting\.executescript/.test(
      message,
    )
  ) {
    return {
      status: "permission_required",
      code: "ORIGIN_PERMISSION_REQUIRED",
      message: "扩展需要该站点的读取权限",
    };
  }
  return {
    status: "failed",
    code: "SYNC_FAILED",
    message: DIAGNOSTIC_STAGE_LABELS[diagnosticStage]
      ? `登录态同步失败（阶段：${DIAGNOSTIC_STAGE_LABELS[diagnosticStage]}）`
      : "登录态同步失败",
  };
}
