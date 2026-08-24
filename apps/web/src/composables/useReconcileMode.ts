/**
 * 消失渠道对账模式 — 对应后端 routers/settings.py。
 * disable（默认）：停用 / delete：直接删除（需 ConfirmDialog 二次确认）。
 */
import { shallowRef, watch, type Ref } from "vue";
import { api } from "@/lib/api";
import { errorText, useToast } from "./useToast";

export type ReconcileMode = "disable" | "delete";

const reconcileMode = shallowRef<ReconcileMode>("disable");
const pendingDeleteMode = shallowRef(false);
let activated = false;

export function useReconcileMode(enabled: Ref<boolean>) {
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
            const mode = s.data?.main_site_reconcile_mode;
            if (mode === "delete" || mode === "disable") reconcileMode.value = mode;
          })
          .catch(() => {});
      },
      { immediate: true },
    );
  }

  async function persistReconcileMode(mode: ReconcileMode): Promise<void> {
    const prev = reconcileMode.value;
    reconcileMode.value = mode;
    try {
      await api.saveSettings({ main_site_reconcile_mode: mode });
      toast.success(
        mode === "delete"
          ? "已切换：消失渠道将被删除"
          : "已切换：消失渠道将被停用",
      );
    } catch (err) {
      reconcileMode.value = prev;
      toast.error(errorText(err, "设置保存失败"));
    }
  }

  function handleReconcileModeChange(next: ReconcileMode): void {
    if (next === reconcileMode.value) return;
    if (next === "delete") {
      pendingDeleteMode.value = true;
      return;
    }
    void persistReconcileMode("disable");
  }

  return {
    reconcileMode,
    pendingDeleteMode,
    setPendingDeleteMode: (v: boolean) => {
      pendingDeleteMode.value = v;
    },
    persistReconcileMode,
    handleReconcileModeChange,
  };
}
