<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Server,
  Zap,
} from "lucide-vue-next";
import StatCard from "@/components/StatCard.vue";
import PageHeader from "@/components/PageHeader.vue";
import Panel from "@/components/Panel.vue";
import SiteTable from "@/components/SiteTable.vue";
import ChangeTable from "@/components/ChangeTable.vue";
import MainSiteHealthPanel from "@/components/MainSiteHealthPanel.vue";
import { Select } from "@/components/ui";
import { truthy } from "@/lib/format";
import { useConsoleData } from "@/composables/useConsoleData";
import { useAppActions } from "@/composables/useAppActions";

const { sites, changes, selectedId } = useConsoleData();
const {
  handleView,
  openRatios,
  handleCheck,
  openSiteForm,
  confirmDelete,
  handleSyncSession,
} = useAppActions();

const platformFilter = ref("all");
const leftPanelRef = ref<HTMLDivElement | null>(null);
const overviewPanelHeight = ref<number | null>(null);

const enabledCount = computed(
  () => sites.value.filter((site) => truthy(site.enabled)).length,
);
const okCount = computed(
  () =>
    sites.value.filter(
      (site) => truthy(site.enabled) && site.status === "ok",
    ).length,
);
const failedCount = computed(() =>
  sites.value.filter(
    (site) =>
      truthy(site.enabled) &&
      ["warning", "failed"].includes(String(site.status)),
  ).length,
);

const overviewSites = computed(() =>
  platformFilter.value === "all"
    ? sites.value
    : sites.value.filter((site) => site.platform === platformFilter.value),
);

// 让右侧「最近变化」面板跟随左侧「渠道概览」面板高度，避免双栏视觉不齐。
// 仅在 ≥1400px 双栏布局下生效；左侧行数变化时重新测量。
let resizeObserver: ResizeObserver | null = null;

function syncHeight(): void {
  const node = leftPanelRef.value;
  if (!node || typeof ResizeObserver === "undefined") return;
  if (window.innerWidth < 1400) {
    overviewPanelHeight.value = null;
    return;
  }
  overviewPanelHeight.value = Math.round(node.getBoundingClientRect().height);
}

watch(
  () => overviewSites.value.length,
  () => {
    void nextTick(syncHeight);
  },
);

onMounted(() => {
  const node = leftPanelRef.value;
  if (!node || typeof ResizeObserver === "undefined") return;
  resizeObserver = new ResizeObserver(syncHeight);
  resizeObserver.observe(node);
  window.addEventListener("resize", syncHeight);
  syncHeight();
});

onUnmounted(() => {
  resizeObserver?.disconnect();
  resizeObserver = null;
  window.removeEventListener("resize", syncHeight);
});
</script>

<template>
  <div class="upstream-rise flex flex-col gap-6 md:gap-8">
    <PageHeader
      large
      title="上游分组倍率监控"
      subtitle="定时采集 NewAPI / sub2api 上游分组倍率，发现分组、倍率、描述变化，并支持邮件与企业微信推送。"
    />

    <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
      <StatCard label="监控渠道" tone="info">
        <template #value>{{ sites.length }}</template>
        <template #icon><Server :size="17" /></template>
      </StatCard>
      <StatCard label="启用中" tone="brand">
        <template #value>{{ enabledCount }}</template>
        <template #icon><Zap :size="17" /></template>
      </StatCard>
      <StatCard label="正常" tone="brand">
        <template #value>{{ okCount }}</template>
        <template #icon><CheckCircle2 :size="17" /></template>
      </StatCard>
      <StatCard
        label="异常"
        :tone="failedCount > 0 ? 'danger' : 'neutral'"
        :accent="failedCount > 0"
      >
        <template #value>{{ failedCount }}</template>
        <template #icon><AlertTriangle :size="17" /></template>
      </StatCard>
      <StatCard
        class="col-span-2 sm:col-span-1"
        label="最近变化"
        tone="warning"
      >
        <template #value>{{ changes.length }}</template>
        <template #hint>最近 50 条</template>
        <template #icon><Activity :size="17" /></template>
      </StatCard>
    </div>

    <div
      class="grid min-w-0 gap-6 min-[1400px]:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] min-[1400px]:items-start"
    >
      <div ref="leftPanelRef" class="min-w-0">
        <Panel
          class="flex h-full min-w-0 flex-col"
          title="渠道概览"
          :subtitle="`${overviewSites.length} 个渠道 · 最近检测状态和分组数量`"
        >
          <template #action>
            <label
              class="flex items-center gap-2 text-[12.5px] font-medium text-ink-strong"
            >
              <span>平台分类</span>
              <Select v-model="platformFilter" class="w-32">
                <option value="all">全部平台</option>
                <option value="newapi">NewAPI</option>
                <option value="sub2api">sub2api</option>
              </Select>
            </label>
          </template>

          <div class="priceai-scrollbar min-h-0 max-h-[430px] overflow-y-auto pr-1">
            <SiteTable
              :sites="overviewSites"
              :selected-id="selectedId"
              :group-by-platform="true"
              @view="handleView"
              @ratios="openRatios"
              @check="handleCheck"
              @edit="openSiteForm"
              @delete="confirmDelete"
              @sync-session="handleSyncSession"
            />
          </div>
        </Panel>
      </div>

      <div
        class="min-w-0"
        :style="
          overviewPanelHeight
            ? { height: overviewPanelHeight + 'px' }
            : undefined
        "
      >
        <Panel
          class="flex h-full min-w-0 flex-col"
          title="最近变化"
          subtitle="最新倍率和分组变化"
        >
          <div
            class="priceai-scrollbar min-h-0 min-w-0 max-h-[430px] flex-1 overflow-y-auto pr-1 min-[1400px]:max-h-none"
          >
            <ChangeTable :changes="changes" :sites="sites" />
          </div>
        </Panel>
      </div>
    </div>

    <!-- 主站（你自己的中转站）健康总览 -->
    <MainSiteHealthPanel />
  </div>
</template>
