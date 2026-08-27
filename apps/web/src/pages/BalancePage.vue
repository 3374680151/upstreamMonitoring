<script setup lang="ts">
import { computed } from "vue";
import { Coins, RefreshCw, ServerCrash, Wallet } from "lucide-vue-next";
import PageHeader from "@/components/PageHeader.vue";
import Panel from "@/components/Panel.vue";
import Badge from "@/components/Badge.vue";
import StatCard from "@/components/StatCard.vue";
import { Button, EmptyState } from "@/components/ui";
import { fmtTime, platformLabel, usd } from "@/lib/format";
import { useBalances, type BalanceRow } from "@/composables/useBalances";
import { useConsoleData } from "@/composables/useConsoleData";
import type { AccountSubscription, Site, SiteAccount } from "@/lib/types";

const { sites } = useConsoleData();
const { rows, busy, queryOne, queryAll, summary } = useBalances(sites);

/** 各平台的补充信息（NewAPI 看请求数/分组，sub2api 看冻结/充值/RPM） */
function extraText(site: Site, account: SiteAccount): string {
  const parts: string[] = [];
  if (site.platform === "sub2api") {
    if (account.frozen_balance_usd != null) parts.push(`冻结 ${usd(account.frozen_balance_usd)}`);
    if (account.total_recharged_usd != null) parts.push(`充值 ${usd(account.total_recharged_usd)}`);
    if (account.rpm_limit != null && account.rpm_limit !== 0) parts.push(`RPM ${account.rpm_limit}`);
  } else {
    if (account.request_count != null) {
      parts.push(`${Number(account.request_count).toLocaleString("zh-CN")} 次请求`);
    }
    if (account.group) parts.push(`分组 ${account.group}`);
  }
  return parts.join(" · ") || "—";
}

const tableRows = computed(() =>
  sites.value.map((site) => ({
    site,
    row: (rows.value[site.id] || { state: "idle" }) as BalanceRow,
  })),
);

/** sub2api 订阅用量扁平列表（仅在查到订阅时展示） */
const subscriptionItems = computed(() => {
  const items: Array<{ key: string; siteName: string; sub: AccountSubscription }> = [];
  for (const site of sites.value) {
    const row = rows.value[site.id];
    if (row?.state !== "ok" || !row.account.subscriptions?.length) continue;
    row.account.subscriptions.forEach((sub, index) => {
      items.push({
        key: `${site.id}-${sub.name}-${index}`,
        siteName: site.name,
        sub,
      });
    });
  }
  return items;
});

function queryOneSite(site: Site) {
  void queryOne(site);
}
</script>

<template>
  <!-- 无渠道空态 -->
  <div v-if="!sites.length" class="upstream-rise flex flex-col gap-6 md:gap-8">
    <PageHeader title="余额" subtitle="按你配置的渠道站点，用各自登录态查询账户余额" />
    <Panel title="余额" subtitle="暂无渠道">
      <EmptyState
        dense
        title="还没有配置渠道"
        description="先在「渠道监控」添加站点并填写登录信息（NewAPI 浏览器登录态 / 密码 / 令牌，sub2api 账号或登录态）。"
      />
    </Panel>
  </div>

  <!-- 主视图 -->
  <div v-else class="upstream-rise flex flex-col gap-6 md:gap-8">
    <PageHeader
      title="余额"
      subtitle="按你配置的渠道站点，用各自登录态查询：NewAPI /api/user/self · sub2api /api/v1/auth/me"
    />

    <div class="grid gap-3 sm:grid-cols-3">
      <StatCard label="合计余额" tone="brand">
        <template #value>{{ summary.okCount ? usd(summary.total) : "—" }}</template>
        <template #hint>{{ summary.okCount ? `${summary.okCount} 个站点已查询` : "点下方「一键查询」" }}</template>
        <template #icon><Wallet :size="17" /></template>
      </StatCard>

      <StatCard label="已查询 / 渠道总数" tone="info">
        <template #value>{{ summary.okCount }} / {{ sites.length }}</template>
        <template #hint>仅成功读取的计入合计</template>
        <template #icon><Coins :size="17" /></template>
      </StatCard>

      <StatCard
        label="读取失败"
        :tone="summary.errCount ? 'warning' : 'neutral'"
        :accent="!!summary.errCount"
      >
        <template #value>{{ summary.errCount }}</template>
        <template #hint>未配置登录或上游返回异常</template>
        <template #icon><ServerCrash :size="17" /></template>
      </StatCard>
    </div>

    <Panel
      title="各渠道余额"
      subtitle="点「查询」单独刷新某个渠道，或点右侧「一键查询」拉取全部"
    >
      <template #action>
        <Button :loading="busy" @click="queryAll">
          <template v-if="busy">查询中…</template>
          <template v-else>
            <RefreshCw :size="13" />
            一键查询
          </template>
        </Button>
      </template>

      <div class="priceai-scrollbar overflow-x-auto pb-1">
        <table class="w-full min-w-max table-auto text-left text-[13px]">
          <thead>
            <tr class="border-b border-line text-[11.5px] font-semibold tracking-[0.04em] text-ink-soft uppercase">
              <th class="pb-2.5 pr-3">渠道</th>
              <th class="pb-2.5 pr-3">平台</th>
              <th class="pb-2.5 pr-3 text-right">余额</th>
              <th class="pb-2.5 pr-3 text-right">已用</th>
              <th class="pb-2.5 pr-3">补充信息</th>
              <th class="pb-2.5 pr-3">更新时间</th>
              <th class="pb-2.5"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in tableRows"
              :key="item.site.id"
              class="border-b border-line-soft transition-colors duration-[var(--motion-fast)] last:border-0 hover:bg-sunken-hover"
            >
              <td class="py-3 pr-3">
                <div class="font-semibold text-ink-strong">{{ item.site.name }}</div>
                <div class="mt-0.5 font-mono text-[11px] text-ink-soft">
                  {{ item.site.base_url }}
                </div>
              </td>
              <td class="py-3 pr-3">
                <Badge tone="neutral">{{ platformLabel(item.site) }}</Badge>
              </td>
              <td class="py-3 pr-3 text-right">
                <span
                  v-if="item.row.state === 'ok'"
                  class="font-serif text-[14.5px] font-semibold tabular text-ink-strong"
                >
                  {{ usd(item.row.account.balance_usd) }}
                </span>
                <span
                  v-else-if="item.row.state === 'loading'"
                  class="skeleton inline-block h-3 w-16 rounded-full"
                />
                <Badge v-else-if="item.row.state === 'error'" tone="warning">失败</Badge>
                <span v-else class="text-[11.5px] text-ink-faint">未查询</span>
              </td>
              <td class="py-3 pr-3 text-right tabular text-ink-muted">
                {{ item.row.state === "ok" ? usd(item.row.account.used_usd) : "—" }}
              </td>
              <td class="py-3 pr-3 text-[12px] text-ink-muted">
                <template v-if="item.row.state === 'ok'">
                  {{ extraText(item.site, item.row.account) }}
                </template>
                <span v-else-if="item.row.state === 'error'" class="text-warning-fg">
                  {{ item.row.message }}
                </span>
                <template v-else>—</template>
              </td>
              <td class="py-3 pr-3 text-[11.5px] text-ink-soft">
                {{ item.row.state === "ok" && item.row.fetchedAt ? fmtTime(item.row.fetchedAt) : "—" }}
              </td>
              <td class="py-3 text-right">
                <Button
                  variant="secondary"
                  size="sm"
                  :loading="item.row.state === 'loading'"
                  :disabled="busy"
                  @click="queryOneSite(item.site)"
                >
                  查询
                </Button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- sub2api 订阅用量：仅在查到订阅时展示 -->
      <div v-if="subscriptionItems.length" class="mt-4 space-y-2">
        <div class="t-micro">订阅用量</div>
        <div
          v-for="item in subscriptionItems"
          :key="item.key"
          class="rounded-[var(--radius-md)] border border-line px-3 py-2.5"
        >
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="font-semibold text-ink-strong">
              {{ item.siteName }} · {{ item.sub.name || "订阅" }}
            </div>
            <div class="flex items-center gap-2">
              <Badge v-if="item.sub.status" tone="neutral">{{ item.sub.status }}</Badge>
              <span v-if="item.sub.expires_at" class="text-[11.5px] text-ink-soft">
                到期 {{ fmtTime(String(item.sub.expires_at)) }}
              </span>
            </div>
          </div>
          <div class="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[12px] tabular text-ink-muted">
            <span>
              日 {{ usd(item.sub.daily_usage_usd) }}{{ item.sub.daily_limit_usd ? ` / ${usd(item.sub.daily_limit_usd)}` : "" }}
            </span>
            <span>
              周 {{ usd(item.sub.weekly_usage_usd) }}{{ item.sub.weekly_limit_usd ? ` / ${usd(item.sub.weekly_limit_usd)}` : "" }}
            </span>
            <span>
              月 {{ usd(item.sub.monthly_usage_usd) }}{{ item.sub.monthly_limit_usd ? ` / ${usd(item.sub.monthly_limit_usd)}` : "" }}
            </span>
          </div>
        </div>
      </div>
    </Panel>
  </div>
</template>
