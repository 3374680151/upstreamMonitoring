/**
 * App 级动作注入 — 页面通过 inject 调用 App.vue 管理的弹窗与操作。
 * 避免把 dialog state 和 handler 通过 props 逐层传递。
 */
import { inject, provide, type InjectionKey } from "vue";
import type { Site } from "@/lib/types";

export interface AppActions {
  openSiteForm: (site?: Site | null) => void;
  openRatios: (site: Site) => void;
  confirmDelete: (site: Site) => void;
  handleCheck: (site: Site) => Promise<void>;
  handleSyncSession: (site: Site) => Promise<void>;
  handleSyncMainSites: (
    adminSiteId?: number,
    opts?: { scope?: "all" | "recognized" | "selected"; channelIds?: number[] },
  ) => Promise<boolean>;
  handleView: (site: Site) => void;
  handleEditSite: (siteId: number) => void;
}

export const appActionsKey: InjectionKey<AppActions> = Symbol("app-actions");

export function useAppActions(): AppActions {
  const actions = inject(appActionsKey);
  if (!actions) throw new Error("useAppActions 必须在 App 组件内使用");
  return actions;
}

export function provideAppActions(actions: AppActions): void {
  provide(appActionsKey, actions);
}
