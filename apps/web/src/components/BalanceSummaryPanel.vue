<script setup lang="ts">
import { computed, toRef } from "vue";
import { RouterLink } from "vue-router";
import Panel from "@/components/Panel.vue";
import Badge from "@/components/Badge.vue";
import { platformLabel } from "@/lib/format";
import { usd, useBalances, type BalanceRow } from "@/composables/useBalances";
import type { Site } from "@/lib/types";

/**
 * 总览页的「各渠道余额」卡片：只列渠道，不在总览页打上游。
 * 查询统一收在「余额」页（右上角「详情 →」过去）。
 */
const props = defineProps<{ sites: Site[] }>();

const sitesRef = toRef(props, "sites");
const { rows, summary } = useBalances(sitesRef);

const subtitle = computed(() =>
  summary.value.queried
    ? `已查 ${summary.value.okCount} / ${props.sites.length} 个渠道${summary.value.errCount ? ` · ${summary.value.errCount} 个失败` : ""}`
    : "余额在「余额」页一键查询，这里只列已配置的渠道",
);

const items = computed(() =>
  props.sites.map((site) => ({
    site,
    row: rows.value[site.id] ?? ({ state: "idle" as const } as BalanceRow),
  })),
);
</script>

<template>
  <Panel title="各渠道余额" :subtitle="subtitle">
    <template #action>
      <RouterLink
        to="/balance"
        class="rounded-[var(--radius-sm)] px-2.5 py-1 text-[13px] font-medium text-accent transition-colors duration-[var(--motion-fast)] hover:text-accent-hover hover:underline"
      >
        详情 →
      </RouterLink>
    </template>

    <div v-if="!sites.length" class="py-6 text-center text-[13px] text-ink-muted">
      还没有配置渠道站点
    </div>
    <template v-else>
      <div class="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span class="t-micro">合计余额</span>
        <span class="font-serif text-[26px] font-semibold tabular text-ink-strong">
          {{ summary.okCount ? usd(summary.total) : "—" }}
        </span>
        <Badge v-if="summary.errCount" tone="warning">
          {{ summary.errCount }} 个读取失败
        </Badge>
      </div>

      <div class="space-y-1.5">
        <div
          v-for="item in items"
          :key="item.site.id"
          class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-[var(--radius-md)] border border-line bg-panel px-3 py-2"
        >
          <div class="flex min-w-0 flex-1 items-center gap-2">
            <span class="truncate text-[13.5px] font-semibold text-ink-strong">
              {{ item.site.name }}
            </span>
            <Badge tone="neutral">{{ platformLabel(item.site) }}</Badge>
          </div>
          <div class="shrink-0 text-[13px]">
            <span v-if="item.row.state === 'ok'" class="font-serif font-semibold tabular text-ink-strong">
              {{ usd(item.row.account.balance_usd) }}
            </span>
            <span v-else-if="item.row.state === 'loading'" class="text-[11.5px] text-ink-muted">
              查询中…
            </span>
            <span v-else-if="item.row.state === 'error'" class="text-[11.5px] text-warning-fg" :title="item.row.message">
              读取失败
            </span>
            <span v-else class="text-[11.5px] text-ink-faint">未查询</span>
          </div>
        </div>
      </div>
    </template>
  </Panel>
</template>
