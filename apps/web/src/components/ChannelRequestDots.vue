<script setup lang="ts">
/**
 * 渠道最近请求首字延迟色点（主站监控页 NewAPI 渠道表）。
 * 形态对齐 PerfBars.vue：10 个固定槽位（左=最新），色点只读 props 不拉数据；
 * 颜色语义：level fast=绿 / normal=橙 / slow=红，stale 请求画同色空心圆环，
 * 窗口内无请求整组灰点，缺位补灰点。数值阈值由后端随响应下发，前端不写死。
 */
import { computed } from "vue";
import { formatTtft, ttftTone } from "@/lib/perf";
import type {
  ChannelRecentRequestEntry,
  ChannelRecentRequestPoint,
  TtftThresholds,
} from "@/lib/types";

const SLOT_COUNT = 10;

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
const TONE_RING: Record<string, string> = {
  success: "border-success-fg",
  warning: "border-warning-fg",
  danger: "border-danger-fg",
};

const dots = computed(() => {
  const points = (props.entry?.requests || []).slice(0, SLOT_COUNT);
  const cells = points.map((point) => {
    const tone = ttftTone(point.level);
    return {
      key: `p-${point.log_id ?? point.created_at ?? ""}`,
      class: point.stale
        ? `border-[1.5px] bg-transparent ${TONE_RING[tone] || "border-ink-faint"}`
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

const containerTitle = computed(() => {
  const fast = Number(props.thresholds?.fast_seconds);
  const slow = Number(props.thresholds?.slow_seconds);
  if (Number.isFinite(fast) && Number.isFinite(slow)) {
    return `最近请求首字延迟：<${fast}s 绿 · ${fast}~${slow}s 橙 · ≥${slow}s 红；空心圆环=旧请求（阈值 .env 可调）`;
  }
  return "最近请求首字延迟（阈值 .env 可调）";
});

const ariaLabel = computed(() => {
  if (props.entry?.state === "error") return "最近请求首字延迟：读取失败";
  const points = props.entry?.requests || [];
  if (!points.length) return windowLabel();
  const counts = { fast: 0, normal: 0, slow: 0 };
  for (const point of points) {
    const level = String(point.level || "");
    if (level === "fast" || level === "normal" || level === "slow") {
      counts[level] += 1;
    }
  }
  return `最近请求首字延迟：绿 ${counts.fast} · 橙 ${counts.normal} · 红 ${counts.slow}`;
});

function pointTitle(point: ChannelRecentRequestPoint): string {
  const basis =
    point.is_stream === false ? "耗时（非流式，无首字记录）" : "首字延迟";
  const parts = [`${basis} ${formatTtft(point.ttft_seconds)}`];
  if (point.model_name) parts.push(String(point.model_name));
  if (point.created_at) {
    parts.push(String(point.created_at).slice(0, 16).replace("T", " "));
  }
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
  if (!props.entry || props.entry.state === "idle") return windowLabel();
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
    class="inline-flex h-4 items-center gap-0.5"
    role="img"
    :title="containerTitle"
    :aria-label="ariaLabel"
  >
    <span
      v-for="dot in dots"
      :key="dot.key"
      :class="[
        'h-1.5 w-1.5 rounded-full',
        dot.class,
        loading && !entry?.requests?.length ? 'animate-pulse' : '',
      ]"
      :title="dot.title"
      aria-hidden="true"
    />
  </span>
</template>
