<script setup lang="ts">
/**
 * 计费核对详情弹窗（设计稿「倍率真实性验证」第 9 节）— 只讲三件事：
 * 1. 钱：上游金额（花了）/ 我的金额（收了）/ 利润（收 − 花，负数红色）；
 * 2. 分组倍率对比：真实（实扣 ÷ 列价成本）vs 上游显示，暗改红字；
 * 3. 上游价卡快照变化（验证二）+ tokens 明细备查。
 */
import { computed } from "vue";
import Badge from "@/components/Badge.vue";
import { Modal } from "@/components/ui";
import type { BillingRequestCheck, PriceChangeNote } from "@/lib/types";
import { fmtTime } from "@/lib/format";

interface Props {
  open: boolean;
  check: BillingRequestCheck | null;
}
const props = defineProps<Props>();
const emit = defineEmits<{ close: [] }>();

function fmtUsd(value: number | null | undefined, digits = 6): string {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return "—";
  }
  return `$${Number(value).toFixed(digits)}`;
}

function fmtX(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return "—";
  }
  return `×${new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 }).format(Number(value))}`;
}

const profit = computed(() => {
  const check = props.check;
  if (!check) return null;
  const m = Number(check.main_usd);
  const u = Number(check.upstream_actual_usd);
  if (!Number.isFinite(m) || !Number.isFinite(u)) return null;
  return m - u;
});

const statusBadge = computed(() => {
  const check = props.check;
  const status = check?.status;
  if (status === "ok") return { tone: "success" as const, label: "正常" };
  if (status === "mismatch") {
    if (check?.reason_codes.includes("upstream_ratio_drift")) {
      return { tone: "danger" as const, label: "暗改倍率" };
    }
    if (check?.reason_codes.includes("negative_margin")) {
      return { tone: "danger" as const, label: "亏本" };
    }
    return { tone: "danger" as const, label: "异常" };
  }
  return { tone: "neutral" as const, label: "无法核对" };
});

const reasonLabels: Record<string, string> = {
  upstream_ratio_drift: "实际收得比显示的多（暗改倍率）",
  negative_margin: "收得比花得少（亏本）",
  ratio_field_missing: "上游显示价卡/倍率缺失",
  no_upstream_log: "上游没有这条日志",
  no_binding: "渠道未绑定上游",
  no_token: "渠道缺少登录态",
  no_log_api: "上游不提供日志接口",
};

/** 分组倍率对比：真实 = 实扣 ÷ 列价成本；两侧齐了才可展示 */
const ratioCompare = computed(() => {
  const check = props.check;
  if (!check) return null;
  const derived = Number(check.upstream_derived_group_ratio);
  const shown = Number(check.published_group_ratio);
  if (!Number.isFinite(derived) || derived <= 0 || !Number.isFinite(shown)) {
    return null;
  }
  return { derived, shown, cheated: check.reason_codes.includes("upstream_ratio_drift") };
});

/** 价卡快照变化（验证二）：详情接口附带的上游最近变化记录 */
const priceNotes = computed<PriceChangeNote[]>(() => {
  const notes = props.check?.price_change_notes;
  return Array.isArray(notes) ? notes : [];
});

function noteValueText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") {
    const parts: string[] = [];
    for (const [key, val] of Object.entries(value as Record<string, unknown>)) {
      if (typeof val === "number" || typeof val === "string") parts.push(`${key} ${val}`);
    }
    return parts.join(" · ") || "—";
  }
  return String(value);
}

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

const metaRows = computed(() => {
  const check = props.check;
  if (!check) return [];
  return [
    { label: "请求时间", value: fmtTime(check.request_at) },
    { label: "主站 / 上游日志 ID", value: `#${check.main_log_id} / ${check.upstream_log_id === null ? "—" : `#${check.upstream_log_id}`}` },
  ];
});
</script>

<template>
  <Modal
    :open="open"
    :title="check ? `核对详情 · ${check.model_name}` : '核对详情'"
    :subtitle="check ? check.upstream_site_name || undefined : undefined"
    wide
    @close="emit('close')"
  >
    <div v-if="check" class="flex flex-col gap-4">
      <div class="flex flex-wrap items-center gap-2">
        <Badge :tone="statusBadge.tone" dot>{{ statusBadge.label }}</Badge>
        <Badge v-for="code in check.reason_codes" :key="code" tone="neutral">
          {{ reasonLabels[code] || code }}
        </Badge>
      </div>

      <!-- 三个数：上游金额 / 我的金额 / 利润 -->
      <div class="grid gap-2.5 sm:grid-cols-3">
        <div class="rounded-[var(--radius-md)] border border-line bg-panel-soft px-3.5 py-3">
          <div class="text-[11px] text-ink-muted">上游金额（花了）</div>
          <div class="mt-1 text-[17px] font-semibold text-ink tabular">{{ fmtUsd(check.upstream_actual_usd) }}</div>
        </div>
        <div class="rounded-[var(--radius-md)] border border-line bg-panel-soft px-3.5 py-3">
          <div class="text-[11px] text-ink-muted">我的金额（收了）</div>
          <div class="mt-1 text-[17px] font-semibold text-ink-strong tabular">{{ fmtUsd(check.main_usd) }}</div>
        </div>
        <div
          class="rounded-[var(--radius-md)] border px-3.5 py-3"
          :class="profit === null ? 'border-line bg-panel-soft' : profit < 0 ? 'border-danger-fg/30 bg-danger-bg' : 'border-success-fg/30 bg-success-bg'"
        >
          <div class="text-[11px] text-ink-muted">利润（收 − 花）</div>
          <div
            class="mt-1 text-[17px] font-semibold tabular"
            :class="profit === null ? 'text-ink-muted' : profit < 0 ? 'text-danger-fg' : 'text-success-fg'"
          >
            {{ profit === null ? "—" : `${profit < 0 ? "-" : ""}$${Math.abs(profit).toFixed(6)}` }}
          </div>
        </div>
      </div>

      <!-- 分组倍率对比（验证一） -->
      <div class="rounded-[var(--radius-md)] border border-line px-3.5 py-3">
        <h4 class="t-micro mb-2">分组倍率对比（真实 = 实扣 ÷ 列价成本）</h4>
        <template v-if="ratioCompare">
          <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span class="text-[15px] font-semibold tabular" :class="ratioCompare.cheated ? 'text-danger-fg' : 'text-ink-strong'">
              真实 {{ fmtX(ratioCompare.derived) }}
            </span>
            <span class="text-[12.5px] text-ink-muted">上游显示 {{ fmtX(ratioCompare.shown) }}</span>
            <span
              class="rounded-full px-2 py-0.5 text-[11px] font-medium"
              :class="ratioCompare.cheated ? 'bg-danger-bg text-danger-fg' : 'bg-success-bg text-success-fg'"
            >
              {{ ratioCompare.cheated ? "暗改倍率（实际收得比显示的多）" : "倍率真实" }}
            </span>
          </div>
          <p class="mt-2 text-[11.5px] leading-relaxed text-ink-muted">
            列价成本 {{ fmtUsd(check.upstream_list_cost_usd) }}（按上游显示价卡 × 本次 tokens 算出的倍率前金额）；
            实扣 {{ fmtUsd(check.upstream_actual_usd) }} ÷ 列价成本 = 真实倍率，比显示的高即多收钱。
          </p>
        </template>
        <p v-else class="text-[12.5px] text-ink-muted">
          上游显示价卡或倍率缺失（ratio_field_missing），无法反推真实倍率。
        </p>
      </div>

      <!-- 价卡快照变化（验证二） -->
      <div>
        <h4 class="t-micro mb-1.5">上游价卡快照变化</h4>
        <template v-if="priceNotes.length">
          <div
            v-for="(note, index) in priceNotes"
            :key="index"
            class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 border-b border-line-soft py-1.5 text-[12.5px]"
          >
            <span class="text-ink">
              <span class="font-medium text-ink-strong">{{ note.group_name || "—" }}</span>
              <span class="ml-1.5 text-[11px] text-ink-muted">{{ note.change_type === "model_ratio_changed" ? "模型价卡" : "分组倍率" }}</span>
            </span>
            <span class="tabular text-ink-muted" :title="`${noteValueText(note.old_value)} → ${noteValueText(note.new_value)}`">
              {{ noteValueText(note.old_value) }} → {{ noteValueText(note.new_value) }}
            </span>
            <span class="text-[11px] text-ink-muted tabular">{{ fmtTime(note.created_at) }}</span>
          </div>
        </template>
        <p v-else class="text-[12px] text-ink-muted">暂无变化记录（上游价卡与上次快照一致，或监控刚开始）。</p>
      </div>

      <!-- 请求参数（对账用） -->
      <div>
        <h4 class="t-micro mb-1.5">请求参数</h4>
        <div class="grid gap-x-6 gap-y-1.5 text-[12.5px] sm:grid-cols-2">
          <div v-for="row in tokenRows" :key="row.label" class="flex items-baseline justify-between gap-3 border-b border-line-soft py-1">
            <span class="text-ink-muted">{{ row.label }}</span>
            <span class="text-ink tabular">{{ row.value }}</span>
          </div>
          <div v-for="row in metaRows" :key="row.label" class="flex items-baseline justify-between gap-3 border-b border-line-soft py-1">
            <span class="text-ink-muted">{{ row.label }}</span>
            <span class="text-ink-strong tabular">{{ row.value }}</span>
          </div>
        </div>
      </div>
    </div>
  </Modal>
</template>
