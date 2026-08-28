/**
 * 共享数据层 — 站点列表 / 变化记录 / 推送设置。
 *
 * 模块级单例：App.vue 传 enabled 激活（拉取 + 15s 定时器），
 * 页面不传 enabled 只读状态。所有组件拿到同一份 reactive 数据。
 */
import { shallowRef, watch, type Ref } from "vue";
import { api } from "@/lib/api";
import type { Change, NotificationSettings, Site } from "@/lib/types";

const sites = shallowRef<Site[]>([]);
const changes = shallowRef<Change[]>([]);
const notify = shallowRef<NotificationSettings | null>(null);
const selectedId = shallowRef<number | null>(null);
const error = shallowRef("");
const loading = shallowRef(true);

let prevEnabled = false;
let activated = false;

async function refresh(): Promise<void> {
  try {
    const [sitesResp, changesResp, notifyResp] = await Promise.all([
      api.sites(),
      // 总览「最近变化」需要尽量完整地展示全部变化（固定高度内滚动），多拉一些
      api.changes(500),
      api.notificationSettings(),
    ]);
    const nextSites = sitesResp.data || [];
    sites.value = nextSites;
    changes.value = changesResp.data || [];
    notify.value = notifyResp.data || {};
    if (selectedId.value && !nextSites.some((s) => s.id === selectedId.value)) {
      selectedId.value = nextSites[0]?.id ?? null;
    } else if (!selectedId.value && nextSites.length) {
      selectedId.value = nextSites[0].id;
    }
    error.value = "";
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

function reload(): void {
  loading.value = true;
  void refresh();
}

function setSelectedId(id: number | null): void {
  selectedId.value = id;
}

export function useConsoleData(enabled?: Ref<boolean>) {
  if (enabled && !activated) {
    activated = true;
    watch(
      enabled,
      (val, _old, onCleanup) => {
        if (!val) {
          prevEnabled = false;
          return;
        }
        if (!prevEnabled) {
          prevEnabled = true;
          loading.value = true;
        }
        void refresh();
        const timer = window.setInterval(() => {
          void refresh().catch(() => {});
        }, 15000);
        onCleanup(() => window.clearInterval(timer));
      },
      { immediate: true },
    );
  }

  return {
    sites,
    changes,
    notify,
    loading,
    error,
    selectedId,
    setSelectedId,
    refresh,
    reload,
  };
}
