<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import GroupSummaryBar from "@/components/GroupSummaryBar.vue";
import { api } from "@/lib/api";
import { fmtTime, groupPropertyText, ratioXText } from "@/lib/format";
import {
  buildPerfMap,
  modelInGroup,
  type PerfSummaryModel,
  type PricingResponse,
} from "@/lib/perf";
import type { ChannelMatchedGroup, GroupItem, Site } from "@/lib/types";

/**
 * 渠道「当前 key 上游倍率」的 hover 浮层：
 * 鼠标移入倍率区域自动展开、移出自动收起。
 * 直接展示同源监控站点的全量分组目录（与倍率弹窗一致的分组、
 * 倍率、模型汇总和属性），并高亮当前 key 命中的分组。
 */

interface Props {
  /** 同 Base URL 的监控站点；无对应站点时为 null */
  site: Site | null;
  /** 当前 key 命中的上游分组（无目录时的兜底展示） */
  matchedGroups: ChannelMatchedGroup[];
}
const props = defineProps<Props>();

const POPOVER_MAX_WIDTH = 920;
const VIEWPORT_MARGIN = 8;

const open = ref(false);
const triggerEl = ref<HTMLElement | null>(null);
const popoverEl = ref<HTMLElement | null>(null);
const style = ref<Record<string, string>>({});
const pricing = shallowRef<PricingResponse | null>(null);
const perfModels = shallowRef<PerfSummaryModel[]>([]);
const detailLoading = ref(false);
const detailError = ref("");
let loadedSiteId: number | null = null;

let openTimer = 0;
let closeTimer = 0;

const matchedNames = computed(
  () => new Set(props.matchedGroups.map((item) => item.name)),
);

interface CatalogRow {
  name: string;
  item: GroupItem;
  matched: boolean;
}

/** 目录行：保留倍率弹窗的全量分组排序，命中项只高亮、不改变目录顺序。 */
const catalogRows = computed<CatalogRow[]>(() => {
  const loginGroups = props.site?.current_login_groups || {};
  const groups =
    props.site?.login_enabled && Object.keys(loginGroups).length
      ? loginGroups
      : props.site?.current_groups || {};
  const entries = Object.entries(groups);
  return entries
    .map(([name, item]) => ({ name, item, matched: matchedNames.value.has(name) }))
    .sort((a, b) => {
      const ratioDiff =
        (Number.isFinite(Number(a.item?.ratio)) ? Number(a.item.ratio) : Infinity) -
        (Number.isFinite(Number(b.item?.ratio)) ? Number(b.item.ratio) : Infinity);
      if (ratioDiff) return ratioDiff;
      return a.name.localeCompare(b.name, "zh-CN");
    });
});

const perfMap = computed(() =>
  buildPerfMap({ data: { models: perfModels.value } }),
);

function groupSummary(name: string) {
  const models = (pricing.value?.data || []).filter((model) =>
    modelInGroup(model, name),
  );
  const measured = models
    .map((model) => perfMap.value.get(model.model_name))
    .filter((model): model is PerfSummaryModel => Boolean(model));
  const average = (values: Array<number | undefined>): number | null => {
    const valid: number[] = values.flatMap((value) =>
      Number.isFinite(Number(value)) ? [Number(value)] : [],
    );
    return valid.length
      ? valid.reduce((sum, value) => sum + value, 0) / valid.length
      : null;
  };
  const samples = measured
    .map((model) => Number(model.request_count))
    .filter((value) => Number.isFinite(value));
  return {
    modelCount: models.length,
    monitoredCount: measured.length,
    successRate: average(measured.map((model) => model.success_rate)),
    avgLatencyMs: average(measured.map((model) => model.avg_latency_ms)),
    avgTps: average(measured.map((model) => model.avg_tps)),
    sampleCount: samples.length
      ? samples.reduce((sum, value) => sum + value, 0)
      : null,
  };
}

function clearTimers(): void {
  window.clearTimeout(openTimer);
  window.clearTimeout(closeTimer);
}

function scheduleOpen(): void {
  clearTimers();
  openTimer = window.setTimeout(() => {
    positionPopover();
    open.value = true;
    void loadDetails();
  }, 100);
}

function scheduleClose(): void {
  clearTimers();
  closeTimer = window.setTimeout(() => {
    open.value = false;
  }, 150);
}

function cancelClose(): void {
  window.clearTimeout(closeTimer);
}

function closeNow(): void {
  clearTimers();
  open.value = false;
}

function positionPopover(): void {
  const el = triggerEl.value;
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const width = Math.min(
    POPOVER_MAX_WIDTH,
    window.innerWidth - 2 * VIEWPORT_MARGIN,
  );
  const left = Math.min(
    Math.max(rect.left, VIEWPORT_MARGIN),
    window.innerWidth - width - VIEWPORT_MARGIN,
  );
  const maxHeight = Math.min(
    620,
    Math.max(260, window.innerHeight - 2 * VIEWPORT_MARGIN - 16),
  );
  style.value = {
    left: `${Math.max(left, VIEWPORT_MARGIN)}px`,
    top: `${rect.bottom + 6}px`,
    width: `${width}px`,
    maxHeight: `${maxHeight}px`,
  };
  // 先渲染再量实际高度：下方放不下时翻到触发元素上方。
  void nextTick(() => {
    const pop = popoverEl.value;
    if (!pop) return;
    const height = pop.offsetHeight;
    if (rect.bottom + 6 + height > window.innerHeight - VIEWPORT_MARGIN) {
      style.value = {
        ...style.value,
        top: `${Math.max(VIEWPORT_MARGIN, rect.top - height - 6)}px`,
      };
    }
  });
}

async function loadDetails(): Promise<void> {
  const site = props.site;
  if (
    !site ||
    site.platform !== "newapi" ||
    loadedSiteId === site.id ||
    detailLoading.value
  ) {
    return;
  }
  detailLoading.value = true;
  detailError.value = "";
  pricing.value = null;
  perfModels.value = [];
  try {
    const [pricingResult, perfResult] = await Promise.allSettled([
      api.sitePricing(site.id),
      api.sitePerfSummary(site.id, 1),
    ]);
    if (loadedSiteId === site.id || props.site?.id !== site.id) return;
    if (pricingResult.status === "fulfilled") {
      pricing.value = pricingResult.value;
    } else {
      detailError.value = "模型清单读取失败";
    }
    if (perfResult.status === "fulfilled") {
      perfModels.value = perfResult.value.data?.models || [];
    } else if (!detailError.value) {
      detailError.value = "模型状态读取失败";
    }
    if (pricingResult.status === "fulfilled") loadedSiteId = site.id;
  } finally {
    detailLoading.value = false;
  }
}

function onViewportChange(): void {
  if (open.value) closeNow();
}

onMounted(() => {
  window.addEventListener("scroll", onViewportChange, true);
  window.addEventListener("resize", closeNow);
});

onBeforeUnmount(() => {
  window.removeEventListener("scroll", onViewportChange, true);
  window.removeEventListener("resize", closeNow);
  clearTimers();
});
</script>

<template>
  <span
    ref="triggerEl"
    class="inline-block max-w-full cursor-help"
    @mouseenter="scheduleOpen"
    @mouseleave="scheduleClose"
  >
    <slot />
  </span>
  <Teleport to="body">
    <div
      v-if="open"
      ref="popoverEl"
      role="tooltip"
      class="priceai-scrollbar fixed z-40 overflow-y-auto rounded-[var(--radius-md)] border border-line bg-panel p-3 shadow-[var(--shadow-floating)]"
      :style="style"
      @mouseenter="cancelClose"
      @mouseleave="scheduleClose"
    >
      <template v-if="site">
        <div class="flex items-center justify-between gap-2">
          <div class="shrink-0 text-[11px] font-bold text-ink-muted">
            认证可见分组 · {{ catalogRows.length }} 个
          </div>
          <div class="truncate text-[11px] text-ink-soft" :title="site.name">
            {{ site.name }} · 上次检测 {{ fmtTime(site.last_check_at) }}
          </div>
        </div>
        <div class="mt-2 overflow-x-auto">
          <table class="w-full min-w-[780px] table-fixed text-left text-[12px]">
            <colgroup>
              <col class="w-[170px]" />
              <col class="w-[76px]" />
              <col class="w-[330px]" />
              <col />
            </colgroup>
            <thead class="sticky top-0 bg-panel">
              <tr class="border-b border-line-soft text-[11px] font-semibold text-ink-muted">
                <th class="px-2 py-2">分组</th>
                <th class="px-2 py-2 text-right">倍率</th>
                <th class="px-2 py-2">模型状态 / 倍率</th>
                <th class="px-2 py-2">属性</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in catalogRows"
                :key="`catalog-${row.name}`"
                :class="[
                  'border-b border-line-soft align-top last:border-0',
                  row.matched ? 'bg-accent-soft' : 'hover:bg-sunken-hover',
                ]"
              >
                <td class="px-2 py-2 font-semibold text-ink-strong">
                  <div class="flex items-center gap-1.5">
                    <span class="truncate">{{ row.name }}</span>
                    <span v-if="row.matched" class="shrink-0 text-[10px] font-semibold text-accent">当前 key</span>
                  </div>
                </td>
                <td class="px-2 py-2 text-right font-extrabold tabular-nums text-ink-strong">
                  {{ ratioXText(row.item) }}
                </td>
                <td class="px-2 py-2">
                  <GroupSummaryBar
                    v-if="pricing"
                    :summary="groupSummary(row.name)"
                    :collapsed="false"
                  />
                  <span v-else-if="detailLoading" class="text-[11px] text-ink-soft">正在读取模型清单…</span>
                  <span v-else class="text-[11px] text-ink-soft">{{ detailError || "暂无模型汇总" }}</span>
                </td>
                <td class="px-2 py-2 text-[11px] leading-relaxed text-ink-muted">
                  {{ groupPropertyText(row.item) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <template v-else>
        <div class="text-[11px] font-bold text-ink-muted">当前 key 命中分组</div>
        <div class="mt-1 space-y-0.5">
          <div
            v-for="item in matchedGroups"
            :key="`matched-${item.name}`"
            class="flex items-center justify-between gap-3 rounded-[var(--radius-sm)] bg-sunken px-2 py-1 text-[12.5px]"
            :title="item.desc || item.name"
          >
            <div class="min-w-0">
              <div class="truncate font-semibold text-ink-strong">
                {{ item.name }}
                <span
                  v-if="item.available_to_login === false"
                  class="ml-1 text-[10px] font-semibold text-warning-fg"
                >上游未见此分组</span>
              </div>
              <div v-if="item.desc" class="truncate text-[11px] text-ink-soft">
                {{ item.desc }}
              </div>
            </div>
            <span class="shrink-0 font-extrabold tabular-nums text-ink-strong">
              {{ ratioXText(item) }}
            </span>
          </div>
        </div>
        <div
          class="mt-2 rounded-[var(--radius-sm)] bg-sunken px-2 py-1.5 text-[11px] leading-relaxed text-ink-muted"
        >
          未找到同 Base URL 的监控站点，仅展示当前 key 命中分组；
          可在「站点监控」添加该上游后查看全量分组倍率。
        </div>
      </template>
    </div>
  </Teleport>
</template>
