<script setup lang="ts">
import { computed } from "vue";
import {
  effectiveSuccessRate,
  formatMs,
  formatRate,
  formatTps,
  successTone,
  type PerfSummaryModel,
  type PricingModel,
} from "@/lib/perf";
import Badge from "./Badge.vue";
import PerfBars from "./PerfBars.vue";

interface Props {
  model: PricingModel;
  perf?: PerfSummaryModel;
  perfLoading: boolean;
}
const props = defineProps<Props>();

const recentRate = computed(() => effectiveSuccessRate(props.perf));
const tone = computed(() => successTone(recentRate.value));
const status = computed(() => {
  if (props.perfLoading) return "读取中";
  if (recentRate.value == null) return "无样本";
  if (tone.value === "success") return "正常";
  if (tone.value === "warning") return "需关注";
  return "异常";
});
const modelRatio = computed(() => Number(props.model.model_ratio));
const ratio = computed(() =>
  Number.isFinite(modelRatio.value) ? `${modelRatio.value.toFixed(2)}x` : "-",
);
</script>

<template>
  <div class="rounded-xl border border-line bg-panel-soft px-3 py-2">
    <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
      <div class="min-w-0 truncate text-sm font-semibold text-ink-strong">
        {{ model.model_name }}
      </div>
      <div class="flex flex-wrap items-center justify-end gap-x-2 gap-y-1 text-[11px]">
        <Badge :tone="perfLoading ? 'neutral' : tone">{{ status }}</Badge>
        <Badge :tone="perfLoading ? 'neutral' : tone" class="tabular-nums">
          成功率 {{ recentRate == null ? "-" : formatRate(recentRate) }}
        </Badge>
        <span class="tabular-nums text-ink-muted">
          延迟 {{ formatMs(perf?.avg_latency_ms) }}
        </span>
        <span class="tabular-nums text-ink-muted">
          TPS {{ formatTps(perf?.avg_tps) }}
        </span>
        <span class="tabular-nums text-ink-strong">
          {{ ratio }}
        </span>
      </div>
    </div>
    <div class="mt-1 flex flex-wrap items-center gap-3 text-[10px] text-ink-soft">
      <span>样本 {{ perf?.request_count == null ? "-" : perf.request_count }}</span>
      <PerfBars v-if="perf?.recent_success_rates?.length" :values="perf.recent_success_rates" />
      <span v-else>暂无最近时间桶</span>
      <span v-if="recentRate != null" class="tabular-nums">近桶均值 {{ formatRate(recentRate) }}</span>
    </div>
  </div>
</template>
