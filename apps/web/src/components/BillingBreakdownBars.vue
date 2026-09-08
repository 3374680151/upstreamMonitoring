<script setup lang="ts">
/**
 * 计费核对辅助聚合条 — 问题模型 TOP5 + 红/灰原因分布。
 * 横向条形 + 数值直接标注（不只靠颜色），点击条目向上抛过滤事件。
 */
import { computed } from "vue";
import type { BillingAuditOverview } from "@/lib/types";
import { billingReasonLabel } from "@/lib/format";

interface Props {
  overview: BillingAuditOverview | null;
  modelLimit?: number;
}
const props = withDefaults(defineProps<Props>(), { modelLimit: 5 });
const emit = defineEmits<{
  selectModel: [modelName: string];
  selectReason: [code: string];
}>();

const models = computed(() =>
  (props.overview?.model_breakdown || []).slice(0, props.modelLimit),
);
const reasons = computed(() => props.overview?.reason_breakdown || []);

const maxModelCount = computed(() =>
  Math.max(1, ...models.value.map((m) => m.total_count)),
);
const maxReasonCount = computed(() =>
  Math.max(1, ...reasons.value.map((r) => r.count)),
);

function barWidth(count: number, max: number): string {
  return `${Math.max(4, Math.round((count / max) * 100))}%`;
}
</script>

<template>
  <div class="grid gap-4 md:grid-cols-2">
    <div>
      <h4 class="t-micro mb-2">问题模型 TOP{{ modelLimit }}</h4>
      <div v-if="!models.length" class="py-2 text-[12px] text-ink-muted">暂无数据</div>
      <div v-else class="flex flex-col gap-1.5">
        <button
          v-for="m in models"
          :key="m.model_name"
          type="button"
          class="group flex items-center gap-2 text-left"
          :title="`筛选模型 ${m.model_name}`"
          @click="emit('selectModel', m.model_name)"
        >
          <span class="w-[140px] shrink-0 truncate text-[12px] text-ink" :title="m.model_name">
            {{ m.model_name }}
          </span>
          <span class="relative h-4 flex-1 overflow-hidden rounded-[3px] bg-sunken">
            <span
              class="absolute inset-y-0 left-0 rounded-[3px] transition-all duration-[var(--motion-base)] group-hover:opacity-80"
              :class="m.mismatch_count ? 'bg-danger-fg/70' : 'bg-ink-faint'"
              :style="{ width: barWidth(m.total_count, maxModelCount) }"
            />
          </span>
          <span class="w-20 shrink-0 text-right text-[11.5px] text-ink-muted tabular">
            {{ m.mismatch_count ? `红 ${m.mismatch_count}` : "" }} {{ m.total_count }} 条
          </span>
        </button>
      </div>
    </div>
    <div>
      <h4 class="t-micro mb-2">红 / 灰原因分布</h4>
      <div v-if="!reasons.length" class="py-2 text-[12px] text-ink-muted">暂无异常与不可核对记录</div>
      <div v-else class="flex flex-col gap-1.5">
        <button
          v-for="r in reasons.slice(0, 6)"
          :key="r.code"
          type="button"
          class="group flex items-center gap-2 text-left"
          :title="`筛选原因「${billingReasonLabel(r.code)}」`"
          @click="emit('selectReason', r.code)"
        >
          <span class="w-[140px] shrink-0 truncate text-[12px] text-ink" :title="r.code">
            {{ billingReasonLabel(r.code) }}
          </span>
          <span class="relative h-4 flex-1 overflow-hidden rounded-[3px] bg-sunken">
            <span
              class="absolute inset-y-0 left-0 rounded-[3px] bg-warning-fg/70 transition-all duration-[var(--motion-base)] group-hover:opacity-80"
              :style="{ width: barWidth(r.count, maxReasonCount) }"
            />
          </span>
          <span class="w-20 shrink-0 text-right text-[11.5px] text-ink-muted tabular">{{ r.count }} 条</span>
        </button>
      </div>
    </div>
  </div>
</template>
