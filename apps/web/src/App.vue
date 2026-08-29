<script setup lang="ts">
import { computed, shallowRef } from "vue";
import { useRouter } from "vue-router";
import { Plus, RefreshCw } from "lucide-vue-next";
import AppShell from "@/components/AppShell.vue";
import ToastViewport from "@/components/ToastViewport.vue";
import LoginPage from "@/components/LoginPage.vue";
import SiteFormDialog from "@/components/SiteFormDialog.vue";
import RatiosDialog from "@/components/RatiosDialog.vue";
import SessionLoginAssistDialog from "@/components/SessionLoginAssistDialog.vue";
import { Button, ConfirmDialog } from "@/components/ui";
import { errorText, useToast } from "@/composables/useToast";
import { useAuth } from "@/composables/useAuth";
import { useConsoleData } from "@/composables/useConsoleData";
import { useAutoSessionResync } from "@/composables/useAutoSessionResync";
import { useReconcileMode } from "@/composables/useReconcileMode";
import { provideAppActions } from "@/composables/useAppActions";
import {
  syncWithLoginAssist,
  onLoginAssistSettled,
  loginAssistState,
} from "@/composables/useLoginAssistSync";
import { api } from "@/lib/api";
import { syncSiteBrowserSession } from "@/lib/browserSessionBridge";
import type { Site } from "@/lib/types";

const router = useRouter();
const toast = useToast();

// ---- 鉴权 ----
const { authReady, authRequired, authed, setAuthed, handleLogout } = useAuth();
const dataEnabled = computed(
  () => authReady.value && (!authRequired.value || authed.value),
);

// ---- 共享数据 ----
const { sites, loading, error, setSelectedId, refresh, reload } =
  useConsoleData(dataEnabled);

// ---- 登录态后台自愈：扩展在线时自动重同步已失效的浏览器登录渠道 ----
useAutoSessionResync(dataEnabled, sites, refresh);

// ---- 对账模式 ----
const { pendingDeleteMode, setPendingDeleteMode, persistReconcileMode } =
  useReconcileMode(dataEnabled);

// ---- 页面级 UI 状态 ----
const deleteTarget = shallowRef<Site | null>(null);
const deleteBusy = shallowRef(false);
const deleteError = shallowRef("");
const formOpen = shallowRef(false);
const editing = shallowRef<Site | null>(null);
const ratiosSite = shallowRef<Site | null>(null);

// ---- handlers ----
async function handleSyncMainSites(
  adminSiteId?: number,
  opts?: { scope?: "all" | "recognized" | "selected"; channelIds?: number[] },
): Promise<boolean> {
  try {
    const result = await api.syncMainSites(adminSiteId, opts);
    await refresh();
    const parts: string[] = [];
    if (result.imported) parts.push(`新增 ${result.imported}`);
    if (result.reenabled) parts.push(`恢复 ${result.reenabled}`);
    if (result.disabled) parts.push(`停用 ${result.disabled}`);
    if (result.deleted) parts.push(`删除 ${result.deleted}`);
    if (result.platform_deleted) parts.push(`平台不符删除 ${result.platform_deleted} 个站点`);
    if (result.conflicts) parts.push(`平台冲突 ${result.conflicts}`);
    if (result.excluded) parts.push(`跳过 ${result.excluded} 个未识别渠道`);
    if (result.channels_changed) parts.push("渠道数据已更新");
    if (result.groups_changed) parts.push("分组数据已更新");
    if (result.keys_refreshed) parts.push(`已刷新 ${result.keys_refreshed} 个渠道 key`);
    if (result.keys_changed) parts.push(`${result.keys_changed} 个 key 已变化并重新匹配`);
    if (result.keys_failed) {
      parts.push(`${result.keys_failed} 个 key 刷新失败`);
      if (result.key_errors?.[0]) parts.push(result.key_errors[0]);
    }
    if (result.failed) {
      toast.info(`主站同步完成${parts.length ? "：" + parts.join(" · ") : ""}，${result.failed} 个主站读取失败`);
    } else if (parts.length) {
      toast.success(`主站同步完成：${parts.join(" · ")}`);
    } else {
      toast.success("主站同步完成，渠道已是最新");
    }
    return result.success !== false && !result.failed;
  } catch (err) {
    toast.error(errorText(err, "主站同步失败"));
    return false;
  }
}

async function handleCheck(site: Site): Promise<void> {
  await toast.run(
    async () => {
      const firstResult = await api.checkSite(site.id);
      if (firstResult.success) {
        await refresh();
        return;
      }
      const canSyncBrowser =
        firstResult.browser_sync_required &&
        site.platform === "sub2api" &&
        site.auth_mode === "browser";
      if (!canSyncBrowser) {
        throw new Error(firstResult.message || "检测失败");
      }
      const syncResult = await syncSiteBrowserSession(site.id);
      await refresh();
      if (syncResult.status !== "ready") {
        throw new Error(syncResult.message || syncResult.error_code || "请先在浏览器登录并同步");
      }
      const retryResult = await api.checkSite(site.id);
      await refresh();
      if (!retryResult.success) {
        throw new Error(retryResult.message || "同步后检测仍然失败");
      }
    },
    { success: `已检测「${site.name}」`, failure: `检测「${site.name}」失败` },
  );
}

async function handleSessionSync(site: Site): Promise<void> {
  try {
    // 无登录态时弹窗引导：打开站点页，用户登录后点「我已登录完成」手动继续
    const result = await syncWithLoginAssist(site);
    await refresh();
    // 弹窗仍在引导时不要额外提示，最终结果由 onLoginAssistSettled 在关闭时统一反馈
    if (loginAssistState.open) return;
    if (result.status === "ready") {
      toast.success(`渠道「${site.name}」登录态已同步`);
      return;
    }
    toast.info(result.message || result.error_code || "登录态同步未完成");
  } catch (err) {
    toast.error(errorText(err, "登录态同步失败"));
  }
}

// 登录引导弹窗关闭（成功 / 取消）时的统一反馈
onLoginAssistSettled(async (result, site) => {
  try {
    await refresh();
  } catch {
    // 刷新失败不掩盖弹窗结果
  }
  if (result.status === "ready") {
    toast.success(`渠道「${site?.name ?? ""}」登录态已同步`);
    return;
  }
  toast.info(result.message || result.error_code || "登录态同步未完成");
});

async function confirmDelete(): Promise<void> {
  if (!deleteTarget.value) return;
  deleteBusy.value = true;
  deleteError.value = "";
  try {
    await api.deleteSite(deleteTarget.value.id);
    await refresh();
    toast.success(`已删除渠道「${deleteTarget.value.name}」`);
    deleteTarget.value = null;
  } catch (err) {
    const message = errorText(err, "删除失败");
    deleteError.value = message;
    toast.error(message);
  } finally {
    deleteBusy.value = false;
  }
}

function handleView(site: Site): void {
  setSelectedId(site.id);
  router.push(`/detail/${site.id}`);
}

function handleEditSite(siteId: number): void {
  const target = sites.value.find((s) => s.id === siteId);
  if (!target) {
    toast.info("渠道列表正在刷新，请稍后重试");
    return;
  }
  editing.value = target;
  formOpen.value = true;
}

function openSiteForm(site?: Site | null): void {
  editing.value = site ?? null;
  formOpen.value = true;
}

function openRatios(site: Site): void {
  ratiosSite.value = site;
}

function confirmDeleteSite(site: Site): void {
  deleteError.value = "";
  deleteTarget.value = site;
}

function onLoginSuccess(): void {
  setAuthed(true);
  reload();
}

// ---- 提供给页面 ----
provideAppActions({
  openSiteForm,
  openRatios,
  confirmDelete: confirmDeleteSite,
  handleCheck,
  handleSyncSession: handleSessionSync,
  handleSyncMainSites,
  handleView,
  handleEditSite,
});
</script>

<template>
  <div v-if="!authReady" class="flex min-h-screen items-center justify-center text-[13px] text-ink-muted">
    <span class="inline-flex items-center gap-2">
      <span class="skeleton inline-block h-1.5 w-1.5 rounded-full" />
      正在恢复会话…
    </span>
  </div>

  <LoginPage
    v-else-if="authRequired && !authed"
    @success="onLoginSuccess"
  />

  <AppShell
    v-else
    :site-count="sites.length"
    :on-logout="authRequired ? handleLogout : undefined"
  >
    <template #actions>
      <Button variant="secondary" aria-label="刷新数据" title="刷新数据" @click="refresh">
        <RefreshCw :size="13" />
        <span class="hidden sm:inline">刷新</span>
      </Button>
      <Button variant="brand" aria-label="添加渠道" title="添加渠道" @click="openSiteForm()">
        <Plus :size="13" />
        <span class="hidden sm:inline">添加渠道</span>
      </Button>
    </template>

    <div v-if="error" class="mb-6 rounded-[var(--radius-md)] border border-danger-fg/30 bg-danger-bg px-4 py-3 text-[13px] text-danger-fg">
      <div class="font-semibold">无法连接后端 API</div>
      <div class="mt-0.5 opacity-90">
        {{ error }}。请确认后端已启动（<code class="font-mono">python app.py</code>，默认 :8000）。
      </div>
    </div>

    <div v-if="loading" class="space-y-6">
      <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
        <div v-for="i in 5" :key="i" class="skeleton h-[88px] w-full rounded-[var(--radius-md)]" />
      </div>
      <div class="skeleton h-[320px] w-full rounded-[var(--radius-lg)]" />
    </div>

    <router-view v-else />

    <SiteFormDialog
      v-model:open="formOpen"
      :site="editing"
      @saved="refresh"
      @edit-site="handleEditSite"
    />
    <RatiosDialog
      :open="!!ratiosSite"
      :site="ratiosSite"
      @close="ratiosSite = null"
    />
    <SessionLoginAssistDialog />
    <ConfirmDialog
      :open="!!deleteTarget"
      title="删除渠道"
      confirm-label="删除"
      danger
      :busy="deleteBusy"
      :error="deleteError"
      @confirm="confirmDelete"
      @cancel="deleteTarget = null; deleteError = ''"
    >
      确认删除渠道「<b>{{ deleteTarget?.name }}</b>」？
      <br />
      该渠道的历史快照与变化记录会一并删除，且不可撤销。
    </ConfirmDialog>
    <ConfirmDialog
      :open="pendingDeleteMode"
      title="切换为删除模式"
      confirm-label="切换为删除"
      danger
      @confirm="pendingDeleteMode = false; persistReconcileMode('delete')"
      @cancel="setPendingDeleteMode(false)"
    >
      开启后，主站同步时<b>上游已消失</b>的监控渠道会被<b>永久删除</b>，
      连带其历史快照与变化记录一并删除，且不可恢复。
      <br />
      确认切换为删除模式？
    </ConfirmDialog>
  </AppShell>

  <ToastViewport />
</template>
