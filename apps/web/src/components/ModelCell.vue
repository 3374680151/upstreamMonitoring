<script setup lang="ts">
import { computed } from "vue";
import {
  modelInGroup,
  successTone,
  type PerfSummaryModel,
  type PricingResponse,
} from "@/lib/perf";
import {
  modelMetricText,
  modelStatusLabel,
  modelStatusTone,
  ratioLabel,
} from "@/lib/format";
import type { ModelHealth } from "@/lib/types";
import Badge from "./Badge.vue";
import NewApiModelRow from "./NewApiModelRow.vue";

interface Props {
  siteId: number;
  groupName: string;
  models: Record<string, ModelHealth[]> | null;
  pricing: PricingResponse | null;
  perfMap: Map<string, PerfSummaryModel>;
  isNewApi: boolean;
  perfLoading: boolean;
  error: string;
}
const props = defineProps<Props>();

const expanded = defineModel<Set<string>>("expanded", { required: true });

const newApiList = computed(() => {
  if (!props.pricing) return [];
  return (props.pricing.data || []).filter((model) =>
    modelInGroup(model, props.groupName),
  );
});

const legacyList = computed(() => {
  if (!props.models) return [];
  return props.models[props.groupName] || [];
});

const legacyRows = computed(() =>
  legacyList.value.map((model, index) => {
    const key = `${props.siteId}|${props.groupName}|${model.name}|${index}`;
    const open = expanded.value.has(key);
    const tone = modelStatusTone(model.status);
    const hasAvail =
      model.availability_7d !== null &&
      model.availability_7d !== undefined &&
      Number.isFinite(Number(model.availability_7d));
    const availability = hasAvail
      ? `${Number(model.availability_7d).toFixed(1)}%`
      : model.status === "configured"
        ? "未公开"
        : "-";
    const meta =
      [model.source, model.monitor, model.platform].filter(Boolean).join(" · ") ||
      "-";
    const hasStatusData = Boolean(model.status && model.status !== "configured");
    return { model, index, key, open, tone, hasAvail, availability, meta, hasStatusData };
  }),
);

function toggleExpanded(key: string) {
  const next = new Set(expanded.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  expanded.value = next;
}
</script>

<template>
  <!-- NewAPI: pricing not yet loaded -->
  <span v-if="isNewApi && !pricing" class="text-[12.5px] text-ink-soft">
    {{ error || "正在读取上游模型清单..." }}
  </span>
  <!-- NewAPI: pricing loaded, show model rows -->
  <div v-else-if="isNewApi" class="space-y-2">
    <span v-if="!newApiList.length" class="text-[12.5px] text-ink-soft">
      上游未返回该分组的模型数据
    </span>
    <NewApiModelRow
      v-for="model in newApiList"
      :key="`${groupName}|${model.model_name}`"
      :model="model"
      :perf="perfMap.get(model.model_name)"
      :perf-loading="perfLoading"
    />
  </div>
  <!-- Legacy: models not yet loaded -->
  <span v-else-if="models === null" class="text-[12.5px] text-ink-soft">
    正在读取上游模型...
  </span>
  <!-- Legacy: error -->
  <span v-else-if="error" class="text-[12.5px] text-danger-fg">
    {{ error }}
  </span>
  <!-- Legacy: show model cards -->
  <div v-else class="space-y-2">
    <span v-if="!legacyRows.length" class="text-[12.5px] text-ink-soft">
      上游未返回该分组的模型数据
    </span>
    <div
      v-for="row in legacyRows"
      :key="row.key"
      class="rounded-xl border border-line bg-panel-soft"
    >
      <button
        type="button"
        class="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
        @click="toggleExpanded(row.key)"
      >
        <div class="min-w-0">
          <div class="truncate text-sm font-semibold text-ink-strong">
            {{ row.model.name }}
          </div>
        </div>
        <div class="flex shrink-0 flex-wrap items-center gap-1.5">
          <Badge :tone="row.tone">{{ modelStatusLabel(row.model.status) || "未监控" }}</Badge>
          <Badge :tone="successTone(row.hasAvail ? Number(row.model.availability_7d) : null)">
            可用 {{ row.availability }}
          </Badge>
          <span class="text-[11px] font-bold tabular-nums text-ink-strong">
            {{ ratioLabel(row.model) }}
          </span>
        </div>
      </button>
      <div
        v-if="row.open"
        class="space-y-2 border-t border-line-soft px-3 py-2 text-[11px] text-ink-muted"
      >
        <div>{{ row.meta }}</div>
        <div v-if="row.hasStatusData" class="flex gap-4">
          <span>
            延迟 <b class="text-ink-strong">{{ modelMetricText(row.model.latency_ms) }}</b>
          </span>
          <span>
            PING <b class="text-ink-strong">{{ modelMetricText(row.model.ping_latency_ms) }}</b>
          </span>
        </div>
        <div v-else>上游已返回模型配置，但未公开健康监控数据</div>
      </div>
    </div>
  </div>
</template>
