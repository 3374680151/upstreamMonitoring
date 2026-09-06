<script setup lang="ts">
/**
 * 计费核对详情弹窗 — 展示单条请求的两侧倍率证据与复算金额。
 * 对应 GET /api/billing-audit/requests/{check_id}，由父页面拉数据后传入。
 */
import { computed } from "vue";
import Badge from "@/components/Badge.vue";
import { Modal } from "@/components/ui";
import type { BillingRequestCheck } from "@/lib/types";
import {
  billingMatchLabel,
  billingReasonLabel,
  billingStatusLabel,
  billingStatusTone,
  fmtTime,
  ratioValueText,
  usdPrecise,
} from "@/lib/format";

interface Props {
  open: boolean;
  check: BillingRequestCheck | null;
}
const props = defineProps<Props>();
const emit = defineEmits<{ close: [] }>();

const rows = computed(() => {
  const check = props.check;
  if (!check) return [];
  return [
    { label: "请求时间", value: fmtTime(check.request_at) },
    { label: "模型", value: check.model_name },
    { label: "主站日志 ID", value: String(check.main_log_id) },
    { label: "上游日志 ID", value: check.upstream_log_id === null ? "—" : String(check.upstream_log_id) },
    { label: "匹配状态", value: billingMatchLabel(check.match_status) },
  ];
});

const tokenRows = computed(() => {
  const check = props.check;
  if (!check) return [];
  return [
    { label: "输入 tokens", value: String(check.prompt_tokens) },
    { label: "输出 tokens", value: String(check.completion_tokens) },
    {
      label: "缓存读 tokens",
      value: check.cache_read_tokens === null ? "未知" : String(check.cache_read_tokens),
    },
    {
      label: "缓存写 tokens",
      value: check.cache_creation_tokens === null ? "未知" : String(check.cache_creation_tokens),
    },
  ];
});

const moneyRows = computed(() => {
  const check = props.check;
  if (!check) return [];
  return [
    { label: "官方成本", value: usdPrecise(check.official_usd, 6) },
    { label: "上游期望（按公示倍率）", value: usdPrecise(check.upstream_expected_usd, 6) },
    { label: "上游实扣", value: usdPrecise(check.upstream_actual_usd, 6) },
    { label: "主站实收", value: usdPrecise(check.main_usd, 6) },
  ];
});

const ratioRows = computed(() => {
  const check = props.check;
  if (!check) return [];
  return [
    {
      label: "模型倍率（上游实际 / 公示）",
      value: `${ratioValueText(check.upstream_model_ratio)} / ${ratioValueText(check.published_model_ratio)}`,
    },
    {
      label: "分组倍率（上游实际 / 公示）",
      value: `${ratioValueText(check.upstream_group_ratio)} / ${ratioValueText(check.published_group_ratio)}`,
    },
    {
      label: "补全倍率（上游实际 / 公示）",
      value: `${ratioValueText(check.upstream_completion_ratio)} / ${ratioValueText(check.published_completion_ratio)}`,
    },
    {
      label: "主站倍率（模型 / 分组 / 补全）",
      value: `${ratioValueText(check.main_model_ratio)} / ${ratioValueText(check.main_group_ratio)} / ${ratioValueText(check.main_completion_ratio)}`,
    },
  ];
});

const otherJson = computed(() => {
  const check = props.check;
  if (!check) return "";
  return JSON.stringify(
    { main: check.main_other_json ?? null, upstream: check.upstream_other_json ?? null },
    null,
    2,
  );
});
</script>

<template>
  <Modal
    :open="open"
    :title="check ? `核对详情 · ${check.model_name}` : '核对详情'"
    :subtitle="check ? `主站日志 #${check.main_log_id}` : undefined"
    wide
    @close="emit('close')"
  >
    <div v-if="check" class="flex flex-col gap-4">
      <div class="flex flex-wrap items-center gap-2">
        <Badge :tone="billingStatusTone(check.status)" dot>
          {{ billingStatusLabel(check.status) }}
        </Badge>
        <Badge v-for="code in check.reason_codes" :key="code" tone="neutral">
          {{ billingReasonLabel(code) }}
        </Badge>
      </div>

      <div class="grid gap-x-6 gap-y-1.5 text-[12.5px] sm:grid-cols-2">
        <div v-for="row in rows" :key="row.label" class="flex items-baseline justify-between gap-3 border-b border-line-soft py-1">
          <span class="text-ink-muted">{{ row.label }}</span>
          <span class="text-right text-ink-strong tabular">{{ row.value }}</span>
        </div>
      </div>

      <div>
        <h4 class="t-micro mb-1.5">Tokens 与缓存</h4>
        <div class="grid gap-x-6 gap-y-1.5 text-[12.5px] sm:grid-cols-2">
          <div v-for="row in tokenRows" :key="row.label" class="flex items-baseline justify-between gap-3 border-b border-line-soft py-1">
            <span class="text-ink-muted">{{ row.label }}</span>
            <span class="text-ink tabular">{{ row.value }}</span>
          </div>
        </div>
      </div>

      <div>
        <h4 class="t-micro mb-1.5">金额对照</h4>
        <div class="grid gap-x-6 gap-y-1.5 text-[12.5px] sm:grid-cols-2">
          <div v-for="row in moneyRows" :key="row.label" class="flex items-baseline justify-between gap-3 border-b border-line-soft py-1">
            <span class="text-ink-muted">{{ row.label }}</span>
            <span class="text-ink-strong tabular">{{ row.value }}</span>
          </div>
        </div>
      </div>

      <div>
        <h4 class="t-micro mb-1.5">倍率证据（上游实际 / 我方监控公示）</h4>
        <div class="grid gap-x-6 gap-y-1.5 text-[12.5px] sm:grid-cols-2">
          <div v-for="row in ratioRows" :key="row.label" class="flex items-baseline justify-between gap-3 border-b border-line-soft py-1">
            <span class="text-ink-muted">{{ row.label }}</span>
            <span class="text-ink tabular">{{ row.value }}</span>
          </div>
        </div>
      </div>

      <div>
        <h4 class="t-micro mb-1.5">两侧日志 other 原始 JSON</h4>
        <pre class="max-h-48 overflow-auto rounded-[var(--radius-sm)] border border-line bg-sunken px-3 py-2 text-[11px] leading-relaxed text-ink-muted">{{ otherJson }}</pre>
      </div>
    </div>
  </Modal>
</template>
