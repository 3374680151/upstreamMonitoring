<script setup lang="ts">
/**
 * 渠道汇总 — 按上游站点聚合的核对结果表。
 * 行点击向上抛（页面把明细表筛到该上游）；红率条 = 红数 / 核对数。
 */
import { EmptyState } from "@/components/ui";
import type { BillingUpstreamBreakdown } from "@/lib/types";
import { billingReasonLabel, usdPrecise } from "@/lib/format";

interface Props {
  rows: BillingUpstreamBreakdown[];
  loading?: boolean;
}
defineProps<Props>();
const emit = defineEmits<{ select: [row: BillingUpstreamBreakdown] }>();

function mismatchRate(row: BillingUpstreamBreakdown): number {
  if (!row.total_count) return 0;
  return row.mismatch_count / row.total_count;
}

function ratePercent(row: BillingUpstreamBreakdown): string {
  return `${Math.round(mismatchRate(row) * 100)}%`;
}

function siteName(row: BillingUpstreamBreakdown): string {
  return row.upstream_site_name || `站点 #${row.upstream_site_id ?? "?"}`;
}
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full min-w-[720px] border-collapse text-[12.5px]">
      <thead>
        <tr class="border-b border-line text-left text-[11px] tracking-wide text-ink-muted">
          <th class="px-2.5 py-2 font-medium">上游站点</th>
          <th class="px-2.5 py-2 text-right font-medium">核对数</th>
          <th class="px-2.5 py-2 text-right font-medium">绿</th>
          <th class="px-2.5 py-2 text-right font-medium">红</th>
          <th class="px-2.5 py-2 text-right font-medium">灰</th>
          <th class="w-[140px] px-2.5 py-2 font-medium">红率</th>
          <th class="px-2.5 py-2 text-right font-medium">毛利</th>
          <th class="px-2.5 py-2 font-medium">主要原因</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.upstream_site_id ?? row.upstream_site_name ?? 'unknown'"
          class="border-b border-line-soft transition-colors duration-[var(--motion-fast)]"
          :class="row.upstream_site_id === null ? 'cursor-default' : 'cursor-pointer hover:bg-sunken'"
          :title="row.upstream_site_id === null ? '未识别绑定上游，无法按站点筛选' : `查看「${siteName(row)}」的核对明细`"
          @click="emit('select', row)"
        >
          <td class="max-w-[200px] truncate px-2.5 py-2 font-medium text-ink-strong" :title="siteName(row)">
            {{ siteName(row) }}
          </td>
          <td class="px-2.5 py-2 text-right text-ink tabular">{{ row.total_count }}</td>
          <td class="px-2.5 py-2 text-right text-ink tabular">{{ row.ok_count }}</td>
          <td class="px-2.5 py-2 text-right text-ink tabular" :class="row.mismatch_count ? 'font-medium text-danger-fg' : ''">
            {{ row.mismatch_count }}
          </td>
          <td class="px-2.5 py-2 text-right text-ink-muted tabular">{{ row.unknown_count }}</td>
          <td class="px-2.5 py-2">
            <div class="flex items-center gap-2">
              <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-sunken">
                <div
                  class="h-full rounded-full bg-danger-fg"
                  :style="{ width: `${Math.max(mismatchRate(row) * 100, row.mismatch_count ? 4 : 0)}%` }"
                />
              </div>
              <span class="w-8 shrink-0 text-right text-[11.5px] text-ink-muted tabular">{{ ratePercent(row) }}</span>
            </div>
          </td>
          <td class="px-2.5 py-2 text-right text-ink tabular" :class="row.margin_usd < 0 ? 'text-danger-fg' : ''">
            {{ usdPrecise(row.margin_usd) }}
          </td>
          <td class="max-w-[160px] truncate px-2.5 py-2 text-ink-muted" :title="row.top_reason_code ? billingReasonLabel(row.top_reason_code) : undefined">
            {{ row.top_reason_code ? billingReasonLabel(row.top_reason_code) : "—" }}
          </td>
        </tr>
      </tbody>
    </table>
    <EmptyState
      v-if="!loading && !rows.length"
      title="暂无上游聚合数据"
      description="有核对记录后，这里会按上游站点汇总正常 / 异常 / 无法核对的分布。"
    />
  </div>
</template>
