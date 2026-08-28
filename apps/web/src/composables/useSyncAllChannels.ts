/**
 * 主站同步范围开关 — 对应后端 routers/settings.py 的 main_site_sync_all。
 * 开（默认）：同步主站全部渠道；关：只同步识别为 NewAPI/sub2api 的渠道。
 */
import { shallowRef, watch, type Ref } from "vue";
import { api } from "@/lib/api";
import { errorText, useToast } from "./useToast";

const syncAllChannels = shallowRef(true);
let activated = false;

export function useSyncAllChannels(enabled: Ref<boolean>) {
  const toast = useToast();

  if (!activated) {
    activated = true;
    watch(
      enabled,
      (val) => {
        if (!val) return;
        api
          .getSettings()
          .then((s) => {
            if (typeof s.data?.main_site_sync_all === "boolean") {
              syncAllChannels.value = s.data.main_site_sync_all;
            }
          })
          .catch(() => {});
      },
      { immediate: true },
    );
  }

  async function handleSyncAllChange(next: boolean): Promise<void> {
    if (next === syncAllChannels.value) return;
    const prev = syncAllChannels.value;
    syncAllChannels.value = next;
    try {
      await api.saveSettings({ main_site_sync_all: next });
      toast.success(
        next
          ? "已开启：主站同步导入全部渠道"
          : "已关闭：仅同步识别为 NewAPI / sub2api 的渠道",
      );
    } catch (err) {
      syncAllChannels.value = prev;
      toast.error(errorText(err, "设置保存失败"));
    }
  }

  return {
    syncAllChannels,
    handleSyncAllChange,
  };
}
