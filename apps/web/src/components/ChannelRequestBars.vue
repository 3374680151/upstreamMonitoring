<script setup lang="ts">
/**
 * 渠道最近请求首字延迟条（主站监控页 NewAPI 渠道表）。
 * 形态：10 个固定槽位的细竖条（左=最新），只读 props 不拉数据；
 * 颜色语义沿用旧色点版：level fast=绿 / normal=橙 / slow=红，
 * stale 旧请求降透明度，窗口内无请求整组灰条。
 * 右侧「可用」= 最近 30 分钟内首字达标（绿色档）请求占比，由前端按
 * age_seconds 过滤计算，不进后端契约；数值阈值由后端随响应下发。
 */
import { computed } from "vue";
import { fmtTime } from "@/lib/format";
import { formatTtft, ttftTone } from "@/lib/perf";
import type {
  ChannelRecentRequestEntry,
  ChannelRecentRequestPoint,
  TtftThresholds,
} from "@/lib/types";

const SLOT_COUNT = 10;
/** 可用率统计窗口（秒）：最近 30 分钟 */
const RATE_WINDOW_SECONDS = 1800;

interface Props {
  entry?: ChannelRecentRequestEntry | null;
  thresholds?: TtftThresholds | null;
  loading?: boolean;
}
const props = defineProps<Props>();

const TONE_SOLID: Record<string, string> = {
  success: "bg-success-fg",
  warning: "bg-warning-fg",
  danger: "bg-danger-fg",
};

const bars = computed(() => {
  const points = (props.entry?.requests || []).slice(0, SLOT_COUNT);
  const cells = points.map((point) => {
    const tone = ttftTone(point.level);
    return {
      key: `p-${point.log_id ?? point.created_at ?? ""}`,
      class: point.stale
        ? `${TONE_SOLID[tone] || "bg-sunken-active"} opacity-45`
        : TONE_SOLID[tone] || "bg-sunken-active",
      title: pointTitle(point),
    };
  });
  while (cells.length < SLOT_COUNT) {
    cells.push({
      key: `empty-${cells.length}`,
      class: "bg-sunken-active",
      title: emptyTitle(),
    });
  }
  return cells;
});

type Availability = { ratio: number; hits: number; total: number };

/** 可用率 = 最近 30 分钟内 level=fast（绿档）请求数 / 同窗请求总数；窗口内无请求不展示 */
const availability = computed<Availability | null>(() => {
  const recent = (props.entry?.requests || []).filter(
    (point) => Number(point.age_seconds) <= RATE_WINDOW_SECONDS,
  );
  if (!recent.length) return null;
  const hits = recent.filter((point) => point.level === "fast").length;
  return {
    ratio: (hits / recent.length) * 100,
    hits,
    total: recent.length,
  };
});

const availabilityText = computed(() =>
  availability.value ? `可用 ${availability.value.ratio.toFixed(1)}%` : "",
);

const availabilityTitle = computed(() => {
  if (!availability.value) return "";
  const fast = Number(props.thresholds?.fast_seconds);
  const rule = Number.isFinite(fast)
    ? `首字 < ${fast}s（绿档）计为可用`
    : "首字达标（绿色档）计为可用";
  return `最近 30 分钟可用 ${availability.value.ratio.toFixed(1)}%（${availability.value.hits}/${availability.value.total}）：${rule}`;
});

const containerTitle = computed(() => {
  const fast = Number(props.thresholds?.fast_seconds);
  const slow = Number(props.thresholds?.slow_seconds);
  const legend =
    Number.isFinite(fast) && Number.isFinite(slow)
      ? `<${fast}s 绿 · ${fast}~${slow}s 橙 · ≥${slow}s 红`
      : "绿=快 / 橙=中 / 红=慢";
  return `最近请求首字延迟（左=最新）：${legend}；半透明=旧请求（非实时），灰=无请求；阈值可在 .env 调整`;
});

const ariaLabel = computed(() => {
  if (props.entry?.state === "error") return "最近请求首字延迟：读取失败";
  const points = props.entry?.requests || [];
  if (!points.length) {
    if (!props.entry) return "最近请求首字延迟：暂无数据";
    return windowLabel();
  }
  const counts = { fast: 0, normal: 0, slow: 0 };
  for (const point of points) {
    const level = String(point.level || "");
    if (level === "fast" || level === "normal" || level === "slow") {
      counts[level] += 1;
    }
  }
  const summary = `最近请求首字延迟：绿 ${counts.fast} · 橙 ${counts.normal} · 红 ${counts.slow}`;
  return availability.value
    ? `${summary} · 最近 30 分钟可用 ${availability.value.ratio.toFixed(1)}%`
    : summary;
});

function pointTitle(point: ChannelRecentRequestPoint): string {
  const parts = [
    point.is_stream === false
      ? `耗时 ${formatTtft(point.ttft_seconds)}（非流式，无首字记录）`
      : `延迟 ${formatTtft(point.ttft_seconds)}`,
  ];
  if (point.model_name) parts.push(String(point.model_name));
  if (point.created_at) parts.push(fmtTime(String(point.created_at)));
  if (point.stale) {
    parts.push(`约 ${durationLabel(Number(point.age_seconds))}前的请求（非实时）`);
  }
  return parts.join(" · ");
}

function emptyTitle(): string {
  if (props.entry?.state === "error") {
    return props.entry.error || "读取请求日志失败";
  }
  if (props.loading && !props.entry) return "正在读取最近请求...";
  if (!props.entry) return "暂无数据";
  if (props.entry.state === "idle") return windowLabel();
  return "窗口内没有更多请求";
}

function windowLabel(): string {
  const seconds = Number(props.thresholds?.window_seconds);
  if (Number.isFinite(seconds) && seconds > 0) {
    if (seconds % 3600 === 0) return `最近 ${seconds / 3600} 小时内无请求`;
    if (seconds >= 60) return `最近 ${Math.round(seconds / 60)} 分钟内无请求`;
  }
  return "最近 1 小时内无请求";
}

function durationLabel(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0 分钟";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} 分钟`;
  return `${(seconds / 3600).toFixed(1)} 小时`;
}
</script>

<template>
  <span
    v-if="entry?.state === 'error'"
    class="text-[12.5px] text-danger-fg"
    :title="entry?.error || '读取请求日志失败'"
  >—</span>
  <span
    v-else
    class="inline-flex items-center gap-1.5"
    role="img"
    :aria-label="ariaLabel"
  >
    <span class="inline-flex h-4 items-center gap-[2px]" :title="containerTitle">
      <span
        v-for="bar in bars"
        :key="bar.key"
        :class="[
          'h-4 w-[3px] rounded-full',
          bar.class,
          loading && !entry?.requests?.length ? 'animate-pulse' : '',
        ]"
        :title="bar.title"
        aria-hidden="true"
      />
    </span>
    <span
      v-if="availabilityText"
      class="whitespace-nowrap text-[11px] tabular-nums text-ink-soft"
      :title="availabilityTitle"
    >{{ availabilityText }}</span>
  </span>
</template>
