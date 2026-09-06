<script setup lang="ts">
/**
 * 计费核对明细表 — 页面只传 items，行点击向上抛。
 * 红绿灰：status 直接映射 Badge 色调；灰行把不可核对原因放进悬停提示。
 */
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/ui/EmptyState.vue";
import type { BillingRequestCheck } from "@/lib/types";
import {
  billingMatchLabel,
  billingReasonLabel,
  billingStatusLabel,
  billingStatusTone,
  fmtTime,
  usdPrecise,
} from "@/lib/format";

interface Props {
  items: BillingRequestCheck[];
  loading?: boolean;
}
defineProps<Props>();
const emit = defineEmits<{ open: [check: BillingRequestCheck] }>();

function tokenText(check: BillingRequestCheck): string {
  const parts = [
    `入 ${check.prompt_tokens}`,
    `出 ${check.completion_tokens}`,
  ];
  if (check.cache_read_tokens !== null && check.cache_read_tokens !== undefined) {
    parts.push(`缓存读 ${check.cache_read_tokens}`);
  }
  if (check.cache_creation_tokens) {
    parts.push(`缓存写 ${check.cache_creation_tokens}`);
  }
  return parts.join(" · ");
}

function reasonTitle(check: BillingRequestCheck): string {
  if (!check.reason_codes.length) return billingMatchLabel(check.match_status);
  return check.reason_codes.map((code) => billingReasonLabel(code)).join("；");
}
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full min-w-[860px] border-collapse text-[12.5px]">
      <thead>
        <tr class="border-b border-line text-left text-[11px] tracking-wide text-ink-muted">
          <th class="px-2.5 py-2 font-medium">时间</th>
          <th class="px-2.5 py-2 font-medium">模型</th>
          <th class="px-2.5 py-2 font-medium">上游</th>
          <th class="px-2.5 py-2 font-medium">Tokens</th>
          <th class="px-2.5 py-2 text-right font-medium">官方成本</th>
          <th class="px-2.5 py-2 text-right font-medium">上游实扣</th>
          <th class="px-2.5 py-2 text-right font-medium">主站实收</th>
          <th class="px-2.5 py-2 font-medium">状态</th>
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
          <td class="max-w-[200px] truncate px-2.5 py-2 font-medium text-ink-strong" :title="check.model_name">
            {{ check.model_name }}
          </td>
          <td class="max-w-[140px] truncate px-2.5 py-2 text-ink-muted" :title="check.upstream_site_name || undefined">
            {{ check.upstream_site_name || "-" }}
          </td>
          <td class="whitespace-nowrap px-2.5 py-2 text-ink tabular">
            {{ tokenText(check) }}
          </td>
          <td class="whitespace-nowrap px-2.5 py-2 text-right text-ink tabular">
            {{ usdPrecise(check.official_usd) }}
          </td>
          <td class="whitespace-nowrap px-2.5 py-2 text-right text-ink tabular">
            {{ usdPrecise(check.upstream_actual_usd) }}
          </td>
          <td class="whitespace-nowrap px-2.5 py-2 text-right font-medium text-ink-strong tabular">
            {{ usdPrecise(check.main_usd) }}
          </td>
          <td class="whitespace-nowrap px-2.5 py-2">
            <Badge :tone="billingStatusTone(check.status)" dot :title="reasonTitle(check)">
              {{ billingStatusLabel(check.status) }}
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
