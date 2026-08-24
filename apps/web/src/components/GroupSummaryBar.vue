<script setup lang="ts">
import { computed } from "vue";
import { formatMs, formatRate, formatTps, successTone } from "@/lib/perf";
import Badge from "./Badge.vue";

interface GroupSummary {
  modelCount: number;
  monitoredCount: number;
  successRate: number | null;
  avgLatencyMs: number | null;
  avgTps: number | null;
  sampleCount: number | null;
}

interface Props {
  summary: GroupSummary;
  collapsed: boolean;
}
const props = defineProps<Props>();

const tone = computed(() => successTone(props.summary.successRate));
</script>

<template>
  <div class="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-line-soft pb-2 text-[11px] text-ink-muted">
    <span class="font-semibold text-ink-strong">分组平均</span>
    <Badge :tone="tone">
      {{ summary.successRate == null ? "暂无成功率" : `成功率 ${formatRate(summary.successRate)}` }}
    </Badge>
    <span class="tabular-nums">延迟 {{ formatMs(summary.avgLatencyMs) }}</span>
    <span class="tabular-nums">TPS {{ formatTps(summary.avgTps) }}</span>
    <span class="tabular-nums">
      样本 {{ summary.sampleCount == null ? "-" : summary.sampleCount }}
    </span>
    <span class="tabular-nums">
      模型 {{ summary.monitoredCount }}/{{ summary.modelCount }}
    </span>
    <span v-if="collapsed" class="text-ink-soft">已折叠 · 点击展开</span>
  </div>
</template>
