/**
 * 浏览器登录态自动重同步 — 后台自愈。
 *
 * 控制台打开期间，每次渠道列表刷新后挑出「浏览器登录模式 + 登录态已失效」
 * 的启用渠道，在扩展在线的前提下静默重走一遍 session-sync：
 * - 扩展不在线：静默跳过，不创建同步请求、不改任何状态；
 * - 同一渠道 5 分钟内最多自动尝试一次（内存去抖，页面刷新即重置）；
 * - 任一渠道恢复后刷新列表，徽标自动回到「登录态已同步」。
 */
import { watch, type Ref } from "vue";
import { autoResyncSiteBrowserSession } from "@/lib/browserSessionBridge";
import { truthy } from "@/lib/format";
import type { Site } from "@/lib/types";

const COOLDOWN_MS = 5 * 60 * 1000;
const lastAttempt = new Map<number, number>();
let running = false;

function pendingCandidates(sites: Site[]): Site[] {
  return sites.filter((site) => {
    if (!truthy(site.enabled)) return false;
    if (site.auth_mode !== "browser") return false;
    if (site.session_sync_status !== "expired") return false;
    const last = lastAttempt.get(site.id) || 0;
    return Date.now() - last >= COOLDOWN_MS;
  });
}

async function run(
  sites: Site[],
  refresh: () => Promise<void>,
): Promise<void> {
  if (running) return;
  const queue = pendingCandidates(sites);
  if (!queue.length) return;
  running = true;
  try {
    for (const site of queue) {
      lastAttempt.set(site.id, Date.now());
      await autoResyncSiteBrowserSession(site.id);
    }
    await refresh();
  } finally {
    running = false;
  }
}

export function useAutoSessionResync(
  enabled: Ref<boolean>,
  sites: Ref<Site[]>,
  refresh: () => Promise<void>,
): void {
  watch(
    [sites, enabled],
    ([list, on]) => {
      if (!on || !list?.length) return;
      void run(list, refresh);
    },
    { immediate: true },
  );
}
