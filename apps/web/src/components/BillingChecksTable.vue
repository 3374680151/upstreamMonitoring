<script setup lang="ts">
/**
 * 计费核对明细表 — 每条请求展示：上游金额（花了）、我的金额（收了）、
 * 利润（收 − 花）、上游分组倍率（真实反推 vs 上游显示，暗改标红）。
 * 状态胶囊只给结论（正常 / 暗改倍率 / 亏本 / 无法核对），细节在行详情。
 */
import Badge from "@/components/Badge.vue";
import { EmptyState } from "@/components/ui";
import type { BillingRequestCheck } from "@/lib/types";
import { fmtTime } from "@/lib/format";

interface Props {
  items: BillingRequestCheck[];
  loading?: boolean;
  /** 上游站点 id → 平台（newapi / sub2api），页面从渠道列表映射传入 */
  platformBySiteId?: Record<number, string>;
}
const props = defineProps<Props>();
const emit = defineEmits<{ open: [check: BillingRequestCheck] }>();

const PLATFORM_BADGE: Record<string, { label: string; tone: "success" | "info" }> = {
  newapi: { label: "NewAPI", tone: "success" },
  sub2api: { label: "sub2api", tone: "info" },
};

function platformBadge(check: BillingRequestCheck): {
  label: string;
  tone: "success" | "info";
} | null {
  const platform =
    (check.upstream_site_id !== null &&
      props.platformBySiteId?.[check.upstream_site_id]) ||
    "";
  return PLATFORM_BADGE[platform] || null;
}

function usdCell(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return "—";
  }
  return `$${Number(value).toFixed(5)}`;
}

/** 利润 = 收 − 花；无上游数据时 null */
function profit(check: BillingRequestCheck): number | null {
  const m = Number(check.main_usd);
  const u = Number(check.upstream_actual_usd);
  if (!Number.isFinite(m) || !Number.isFinite(u)) return null;
  return m - u;
}

function profitText(check: BillingRequestCheck): string {
  const p = profit(check);
  return p === null ? "—" : `${p < 0 ? "-" : ""}$${Math.abs(p).toFixed(5)}`;
}

/** 状态胶囊（设计稿第 9 节）：暗改倍率 / 亏本 有独立文案，其余异常归「异常」 */
function statusBadge(check: BillingRequestCheck): {
  tone: "success" | "danger" | "neutral";
  label: string;
} {
  if (check.status === "ok") return { tone: "success", label: "正常" };
  if (check.status === "mismatch") {
    if (check.reason_codes.includes("upstream_ratio_drift")) {
      return { tone: "danger", label: "暗改倍率" };
    }
    if (check.reason_codes.includes("negative_margin")) {
      return { tone: "danger", label: "亏本" };
    }
    return { tone: "danger", label: "异常" };
  }
  return { tone: "neutral", label: "无法核对" };
}

function fmtX(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return "—";
  }
  return `×${new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 }).format(Number(value))}`;
}

/** 倍率列：真实分组倍率（实扣÷列价） vs 上游显示分组倍率；有一侧缺失显示 — */
function hasRatioPair(check: BillingRequestCheck): boolean {
  const derived = Number(check.upstream_derived_group_ratio);
  const shown = Number(check.published_group_ratio);
  return Number.isFinite(derived) && derived > 0 && Number.isFinite(shown) && shown >= 0;
}

/** 真实 > 显示（超容带）→ 暗改红字；其余（一致或便宜）正常色 */
function isRatioCheated(check: BillingRequestCheck): boolean {
  return check.reason_codes.includes("upstream_ratio_drift");
}
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full min-w-[880px] border-collapse text-[12.5px]">
      <thead>
        <tr class="border-b border-line text-left text-[11px] tracking-wide text-ink-muted">
          <th class="px-2.5 py-2 font-medium">时间</th>
          <th class="px-2.5 py-2 font-medium">模型</th>
          <th class="px-2.5 py-2 font-medium">上游</th>
          <th class="px-2.5 py-2 text-right font-medium">上游金额（花了）</th>
          <th class="px-2.5 py-2 text-right font-medium">我的金额（收了）</th>
          <th class="px-2.5 py-2 text-right font-medium">利润</th>
          <th class="px-2.5 py-2 text-right font-medium">倍率（真实 / 显示）</th>
          <th class="px-2.5 py-2 pl-4 font-medium">状态</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="check in items"
          :key="check.id"
          class="cursor-pointer border-b border-line-soft transition-colors duration-[var(--motion-fast)] hover:bg-sunken"
          @click="emit('open', check)"
        >
          <td class="whitespace-nowrap px-2.5 py-2 text-ink-muted tabular">
            {{ fmtTime(check.request_at) }}
          </td>
          <td class="max-w-[160px] truncate px-2.5 py-2 font-medium text-ink-strong" :title="check.model_name">
            {{ check.model_name }}
          </td>
          <td class="whitespace-nowrap px-2.5 py-2">
            <div class="flex items-center gap-1.5">
              <Badge
                v-if="platformBadge(check)"
                :tone="platformBadge(check)!.tone"
                class="!px-1.5 !py-0 !text-[10px]"
              >
                {{ platformBadge(check)!.label }}
              </Badge>
              <span class="max-w-[110px] truncate text-ink" :title="check.upstream_site_name || undefined">
                {{ check.upstream_site_name || "—" }}
              </span>
            </div>
          </td>
          <td class="whitespace-nowrap px-2.5 py-2 text-right text-ink tabular">
            {{ usdCell(check.upstream_actual_usd) }}
          </td>
          <td class="whitespace-nowrap px-2.5 py-2 text-right font-medium text-ink-strong tabular">
            {{ usdCell(check.main_usd) }}
          </td>
          <td
            class="whitespace-nowrap px-2.5 py-2 text-right font-medium tabular"
            :class="profit(check) === null ? 'text-ink-muted' : profit(check)! < 0 ? 'text-danger-fg' : 'text-success-fg'"
          >
            {{ profitText(check) }}
          </td>
          <td class="whitespace-nowrap px-2.5 py-2 text-right tabular">
            <template v-if="hasRatioPair(check)">
              <span
                :class="isRatioCheated(check) ? 'font-semibold text-danger-fg' : 'text-ink'"
              >
                {{ fmtX(check.upstream_derived_group_ratio) }}
              </span>
              <span class="text-[11px] font-normal text-ink-muted">
                / 显示 {{ fmtX(check.published_group_ratio) }}
              </span>
            </template>
            <template v-else>—</template>
          </td>
          <td class="px-2.5 py-2 pl-4">
            <Badge :tone="statusBadge(check).tone" dot>
              {{ statusBadge(check).label }}
            </Badge>
          </td>
        </tr>
      </tbody>
    </table>
    <EmptyState
      v-if="!loading && !items.length"
      title="暂无核对记录"
      description="开启功能并触发一次「立即核对」后，主站的消费请求会出现在这里。"
    />
  </div>
</template>
