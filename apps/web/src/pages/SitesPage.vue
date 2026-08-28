<script setup lang="ts">
import { computed, ref } from "vue";
import PageHeader from "@/components/PageHeader.vue";
import Panel from "@/components/Panel.vue";
import SiteTable from "@/components/SiteTable.vue";
import { Button, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import {
  extensionRequiredMessage,
  probeSessionBridge,
} from "@/lib/browserSessionBridge";
import { syncWithLoginAssist } from "@/composables/useLoginAssistSync";
import type { Platform, SiteFormPayload } from "@/lib/types";
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
// 正在同步登录态的平台（null = 空闲），两个按钮各自显示自己的 loading
const syncingBrowserPlatform = ref<"sub2api" | "newapi" | null>(null);
const browserSyncProgress = ref<{ platform: string; text: string } | null>(null);
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

const hasSub2ApiSite = computed(() =>
  sites.value.some(
    (site) => truthy(site.enabled) && site.platform === "sub2api",
  ),
);
// 只要有启用的 NewAPI 渠道就显示按钮；token / 密码模式不自动切换，
// 避免批量把正在用令牌的渠道切进浏览器模式后同步失败、反而丢掉原认证方式。
const hasNewApiSite = computed(() =>
  sites.value.some(
    (site) => truthy(site.enabled) && site.platform === "newapi",
  ),
);

function toBrowserSwitchPayload(site: {
  name: string;
  base_url: string;
  platform: Platform | string;
  interval_minutes: number;
  enabled: boolean | number;
  system_token_fallback_enabled?: boolean | number;
}): SiteFormPayload {
  return {
    name: site.name,
    platform: (site.platform === "newapi" ? "newapi" : "sub2api") as Platform,
    base_url: site.base_url,
    interval_minutes: site.interval_minutes,
    login_enabled: true,
    auth_mode: "browser",
    login_username: "",
    login_password: "",
    access_token: "",
    refresh_token: "",
    token_expires_at: "",
    access_user_id: "",
    enabled: truthy(site.enabled),
    system_token_fallback_enabled: truthy(site.system_token_fallback_enabled),
  };
}

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

async function syncBrowserSessions(targetPlatform: "sub2api" | "newapi"): Promise<void> {
  if (syncingBrowserPlatform.value) return;
  const targets = sites.value.filter(
    (site) => truthy(site.enabled) && site.platform === targetPlatform,
  );
  const platformLabel = targetPlatform === "sub2api" ? "sub2api" : "NewAPI";
  if (!targets.length) {
    toast.info(`暂无启用的 ${platformLabel} 渠道`);
    return;
  }
  syncingBrowserPlatform.value = targetPlatform;
  const done: string[] = [];
  const failed: string[] = [];
  let index = 0;
  // 实时日志：每完成一个站点就追加一行并刷新列表，让状态即时可见
  const lines: string[] = [`开始同步 ${targets.length} 个 ${platformLabel} 渠道的登录态…`];
  syncResult.value = lines.join("\n");
  try {
    // 先探测扩展连通性：未连接时直接中止，避免逐站创建必失败的同步请求
    const extensionReady = await probeSessionBridge();
    if (!extensionReady) {
      lines.push(`✗ ${extensionRequiredMessage()}`);
      syncResult.value = lines.join("\n");
      toast.error(extensionRequiredMessage());
      return;
    }
    for (const site of targets) {
      index += 1;
      browserSyncProgress.value = { platform: targetPlatform, text: ` ${index}/${targets.length}` };
      const label = `「${site.name}」`;
      try {
        // 非 browser 模式的渠道先切到浏览器登录态（空凭证字段后端保留原值）
        if (site.auth_mode !== "browser") {
          await api.updateSite(site.id, toBrowserSwitchPayload(site));
        }
        // 无登录态时自动弹出站点页引导登录，登录后自动接管
        const result = await syncWithLoginAssist(site);
        if (result.status === "ready") {
          done.push(label);
          lines.push(`✓ ${label} 登录态已同步`);
        } else {
          failed.push(
            `${label}：${result.message || result.error_code || "登录态同步未完成"}`,
          );
          lines.push(
            `✗ ${label} ${result.message || result.error_code || "登录态同步未完成"}`,
          );
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        failed.push(`${label}：${message}`);
        lines.push(`✗ ${label} ${message}`);
      }
      syncResult.value = lines.join("\n");
      // 每站完成后立即刷新列表，登录态状态实时更新
      await refresh();
    }
    lines.push(
      `同步完成：成功 ${done.length} 个，失败 ${failed.length} 个`,
    );
    syncResult.value = lines.join("\n");
  } finally {
    syncingBrowserPlatform.value = null;
    browserSyncProgress.value = null;
    await refresh();
  }
  if (failed.length) {
    toast.info(`登录态同步完成，${failed.length} 个失败，详见同步结果`);
  } else {
    toast.success(`已完成 ${done.length} 个 ${platformLabel} 渠道的登录态同步`);
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
            v-if="hasSub2ApiSite"
            variant="secondary"
            :loading="syncingBrowserPlatform === 'sub2api'"
            title="一键同步所有启用 sub2api 渠道的浏览器登录态；非浏览器登录模式的渠道会自动切换后再同步"
            @click="syncBrowserSessions('sub2api')"
          >
            同步 sub2api 登录态{{
              browserSyncProgress?.platform === "sub2api" ? browserSyncProgress.text : ""
            }}
          </Button>
          <Button
            v-if="hasNewApiSite"
            variant="secondary"
            :loading="syncingBrowserPlatform === 'newapi'"
            title="一键同步所有启用 NewAPI 渠道的浏览器登录态；令牌/密码模式的渠道会自动切换为浏览器登录态后再同步"
            @click="syncBrowserSessions('newapi')"
          >
            同步 NewAPI 登录态{{
              browserSyncProgress?.platform === "newapi" ? browserSyncProgress.text : ""
            }}
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
        <span class="font-semibold text-ink-strong">同步结果</span>
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
