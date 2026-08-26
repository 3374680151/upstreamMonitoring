<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { fmtTime, ratioXText } from "@/lib/format";
import type { ChannelMatchedGroup, GroupItem } from "@/lib/types";

/**
 * 渠道「当前 key 上游倍率」的 hover 浮层：
 * 鼠标移入倍率区域自动展开、移出自动收起。
 * 直接展示同源监控站点的全量分组倍率目录（分组/倍率/描述），
 * 并高亮当前 key 命中的分组；无监控站点时退化为仅展示命中分组。
 */

interface Props {
  /** 同 Base URL 监控站点的分组目录快照；无对应监控站点时为 null */
  catalog: {
    siteName: string;
    fetchedAt: string;
    groups: Record<string, GroupItem>;
  } | null;
  /** 当前 key 命中的上游分组（无目录时的兜底展示） */
  matchedGroups: ChannelMatchedGroup[];
}
const props = defineProps<Props>();

const POPOVER_WIDTH = 360;
const VIEWPORT_MARGIN = 8;

const open = ref(false);
const triggerEl = ref<HTMLElement | null>(null);
const popoverEl = ref<HTMLElement | null>(null);
const style = ref<Record<string, string>>({});

let openTimer = 0;
let closeTimer = 0;

const matchedNames = computed(
  () => new Set(props.matchedGroups.map((item) => item.name)),
);

interface CatalogRow {
  name: string;
  ratio: number | string | null | undefined;
  ratio_type?: string;
  desc: string;
  matched: boolean;
}

/** 目录行：监控站点全部分组，命中分组排最前，其余按倍率升序 */
const catalogRows = computed<CatalogRow[]>(() => {
  const entries = Object.entries(props.catalog?.groups || {});
  return entries
    .map(([name, item]) => ({
      name,
      ratio: item?.ratio,
      ratio_type: item?.ratio_type,
      desc: String(item?.desc || ""),
      matched: matchedNames.value.has(name),
    }))
    .sort((a, b) => {
      if (a.matched !== b.matched) return a.matched ? -1 : 1;
      const ratioDiff =
        (Number.isFinite(Number(a.ratio)) ? Number(a.ratio) : Infinity) -
        (Number.isFinite(Number(b.ratio)) ? Number(b.ratio) : Infinity);
      if (ratioDiff) return ratioDiff;
      return a.name.localeCompare(b.name, "zh-CN");
    });
});

function clearTimers(): void {
  window.clearTimeout(openTimer);
  window.clearTimeout(closeTimer);
}

function scheduleOpen(): void {
  clearTimers();
  openTimer = window.setTimeout(() => {
    positionPopover();
    open.value = true;
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
  const left = Math.min(
    Math.max(rect.left, VIEWPORT_MARGIN),
    window.innerWidth - POPOVER_WIDTH - VIEWPORT_MARGIN,
  );
  const maxHeight = Math.min(
    320,
    Math.max(160, window.innerHeight - 2 * VIEWPORT_MARGIN - 16),
  );
  style.value = {
    left: `${Math.max(left, VIEWPORT_MARGIN)}px`,
    top: `${rect.bottom + 6}px`,
    width: `${POPOVER_WIDTH}px`,
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
      <template v-if="catalog">
        <div class="flex items-center justify-between gap-2">
          <div class="shrink-0 text-[11px] font-bold text-ink-muted">
            上游分组倍率（{{ catalogRows.length }}）
          </div>
          <div class="truncate text-[11px] text-ink-soft" :title="catalog.siteName">
            {{ catalog.siteName }} · 上次检测 {{ fmtTime(catalog.fetchedAt) }}
          </div>
        </div>
        <div class="mt-1 space-y-0.5">
          <div
            v-for="row in catalogRows"
            :key="`catalog-${row.name}`"
            :class="[
              'rounded-[var(--radius-sm)] px-2 py-1 text-[12.5px]',
              row.matched ? 'bg-sunken ring-1 ring-[var(--color-accent-ring)]' : '',
            ]"
            :title="row.desc || row.name"
          >
            <div class="flex items-center justify-between gap-3">
              <span
                :class="[
                  'truncate font-semibold',
                  row.matched ? 'text-accent' : 'text-ink-strong',
                ]"
              >
                {{ row.name }}
              </span>
              <span class="flex shrink-0 items-center gap-1.5">
                <span
                  v-if="row.matched"
                  class="text-[10px] font-semibold text-accent"
                >当前 key</span>
                <span class="font-extrabold tabular-nums text-ink-strong">
                  {{ ratioXText(row) }}
                </span>
              </span>
            </div>
            <div v-if="row.desc" class="mt-0.5 truncate text-[11px] text-ink-soft">
              {{ row.desc }}
            </div>
          </div>
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
