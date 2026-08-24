<script setup lang="ts">
import { computed, ref } from "vue";
import PageHeader from "@/components/PageHeader.vue";
import Panel from "@/components/Panel.vue";
import SiteTable from "@/components/SiteTable.vue";
import { Button, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { truthy } from "@/lib/format";
import { useToast } from "@/composables/useToast";
import { useConsoleData } from "@/composables/useConsoleData";
import { useAppActions } from "@/composables/useAppActions";

type MainSiteSyncRow = {
  admin_site_id?: number;
  platform?: string;
  status?: string;
  channels_count?: number;
  groups_count?: number;
  message?: string;
};

const toast = useToast();
const { sites, refresh, selectedId } = useConsoleData();
const {
  handleView,
  openRatios,
  handleCheck,
  openSiteForm,
  confirmDelete,
  handleSyncSession,
} = useAppActions();

const keyword = ref("");
const status = ref("");
const platform = ref("all");
const syncingAll = ref(false);
const syncingBrowser = ref(false);
const syncResult = ref("");

const filtered = computed(() => {
  const q = keyword.value.trim().toLowerCase();
  return sites.value.filter((site) => {
    if (q && !`${site.name} ${site.base_url}`.toLowerCase().includes(q)) {
      return false;
    }
    const enabled = truthy(site.enabled);
    if (status.value === "disabled" && enabled) return false;
    if (
      status.value &&
      status.value !== "disabled" &&
      (!enabled || site.status !== status.value)
    ) {
      return false;
    }
    if (platform.value !== "all" && site.platform !== platform.value)
      return false;
    return true;
  });
});

const hasBrowserSub2Api = computed(() =>
  sites.value.some(
    (site) =>
      truthy(site.enabled) &&
      site.platform === "sub2api" &&
      site.auth_mode === "browser",
  ),
);

async function syncAllFromMain(): Promise<void> {
  if (syncingAll.value) return;
  syncingAll.value = true;
  syncResult.value = "";
  try {
    const result = await api.syncMainSites();
    if (result.success === false) {
      throw new Error("主站同步失败");
    }
    await refresh();

    const rows = (result.data || []) as MainSiteSyncRow[];
    const syncedRows = rows.filter((row) => row.status === "synced");
    const failedRows = rows.filter(
      (row) =>
        row.status === "sync_failed" ||
        row.status === "fetch_failed" ||
        row.status === "error",
    );
    const summary: string[] = [];
    if (result.channels_changed) summary.push("渠道数据已更新");
    if (result.groups_changed) summary.push("分组数据已更新");
    if (result.imported) summary.push(`新增监控 ${result.imported}`);
    if (result.reenabled) summary.push(`恢复 ${result.reenabled}`);
    if (result.disabled) summary.push(`停用 ${result.disabled}`);
    if (result.deleted) summary.push(`删除 ${result.deleted}`);
    if (result.conflicts) summary.push(`平台冲突 ${result.conflicts}`);
    if (!summary.length) summary.push("渠道和分组已是最新");

    const siteDetails = syncedRows.map(
      (row) =>
        `主站 #${row.admin_site_id ?? "-"}：${row.channels_count ?? 0} 个渠道、${row.groups_count ?? 0} 个分组`,
    );
    const failureDetails = failedRows.map(
      (row) => `主站 #${row.admin_site_id ?? "-"}：${row.message || "读取失败"}`,
    );
    syncResult.value = [...summary, ...siteDetails, ...failureDetails].join(
      "\n",
    );
    if (result.failed) {
      toast.info(`主站同步完成，但有 ${result.failed} 个主站读取失败`);
    } else if (result.conflicts) {
      toast.info(`主站同步完成，但有 ${result.conflicts} 个平台冲突`);
    } else {
      toast.success(`主站同步完成：${summary.join("、")}`);
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    syncResult.value = `主站同步失败：${message}`;
    toast.error(`主站同步失败：${message}`);
  } finally {
    syncingAll.value = false;
  }
}

async function syncSub2ApiBrowserSessions(): Promise<void> {
  if (syncingBrowser.value) return;
  const targets = sites.value.filter(
    (site) =>
      truthy(site.enabled) &&
      site.platform === "sub2api" &&
      site.auth_mode === "browser",
  );
  if (!targets.length) {
    toast.info("暂无配置浏览器登录态的 sub2api 渠道");
    return;
  }
  syncingBrowser.value = true;
  try {
    for (const site of targets) {
      await handleSyncSession(site);
    }
    toast.success(`已完成 ${targets.length} 个 sub2api 渠道的登录态同步`);
  } finally {
    syncingBrowser.value = false;
  }
}
</script>

<template>
  <div class="upstream-rise flex flex-col gap-6 md:gap-8">
    <PageHeader
      title="渠道监控"
      subtitle="盯你的上游渠道站点，每个渠道单独设置平台类型、监控间隔和认证方式。"
    >
      <template #action>
        <div class="flex flex-wrap items-center gap-2">
          <Button
            v-if="hasBrowserSub2Api"
            variant="secondary"
            :loading="syncingBrowser"
            title="仅同步 sub2api 渠道的浏览器登录态"
            @click="syncSub2ApiBrowserSessions"
          >
            同步登录态
          </Button>
          <Button
            variant="brand"
            :loading="syncingAll"
            title="同步所有主站的完整渠道、分组和本地来源关联"
            @click="syncAllFromMain"
          >
            同步主站
          </Button>
        </div>
      </template>
    </PageHeader>

    <div
      v-if="syncResult"
      class="rounded-[var(--radius-sm)] border border-line bg-panel-soft px-3 py-2 text-sm text-ink"
    >
      <div class="mb-1 flex items-center justify-between">
        <span class="font-semibold text-ink-strong">主站同步结果</span>
        <button
          class="text-[11px] text-ink-muted hover:text-ink-strong"
          @click="syncResult = ''"
        >
          关闭
        </button>
      </div>
      <pre
        class="whitespace-pre-wrap text-[12px] leading-relaxed text-ink-muted"
      >{{ syncResult }}</pre>
    </div>

    <Panel
      title="渠道列表"
      :subtitle="`${filtered.length} / ${sites.length} 条`"
    >
      <template #action>
        <div class="flex flex-wrap gap-2">
          <Input
            v-model="keyword"
            class="w-48"
            type="search"
            placeholder="搜索渠道或地址"
          />
          <Select v-model="status" class="w-32">
            <option value="">全部状态</option>
            <option value="ok">正常</option>
            <option value="warning">警告</option>
            <option value="failed">异常</option>
            <option value="disabled">停用</option>
            <option value="unknown">未知</option>
          </Select>
          <Select v-model="platform" class="w-32">
            <option value="all">全部平台</option>
            <option value="newapi">NewAPI</option>
            <option value="sub2api">sub2api</option>
          </Select>
        </div>
      </template>

      <SiteTable
        :sites="filtered"
        :selected-id="selectedId"
        :group-by-platform="true"
        @view="handleView"
        @ratios="openRatios"
        @check="handleCheck"
        @edit="openSiteForm"
        @delete="confirmDelete"
        @sync-session="handleSyncSession"
      />
    </Panel>
  </div>
</template>
