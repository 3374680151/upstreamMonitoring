<script setup lang="ts">
import { computed, shallowRef, watch } from "vue";
import { useRoute } from "vue-router";
import {
  Clock,
  Layers,
  ShieldCheck,
  Timer,
  TrendingDown,
  XCircle,
} from "lucide-vue-next";
import Badge from "@/components/Badge.vue";
import PageHeader from "@/components/PageHeader.vue";
import Panel from "@/components/Panel.vue";
import StatCard from "@/components/StatCard.vue";
import ChangeTable from "@/components/ChangeTable.vue";
import { Button, EmptyState, Select } from "@/components/ui";
import { errorText, useToast } from "@/composables/useToast";
import { useConsoleData } from "@/composables/useConsoleData";
import { api } from "@/lib/api";
import {
  changeDisplayMessage,
  changeTone,
  changeTypeLabel,
  fmtTime,
  platformLabel,
  ratioLabel,
  sessionFailureBadge,
  siteStatusLabel,
  siteStatusTone,
  statusLabel,
  statusTone,
  truthy,
  usd,
} from "@/lib/format";
import { explainUpstreamError } from "@/lib/upstreamError";
import type {
  Change,
  SiteAccount,
  SiteDiscoveryLink,
  SiteSnapshot,
} from "@/lib/types";

/** 从快照的 groups_json 字段解析出分组数量 */
function snapshotGroupCount(snapshot: SiteSnapshot): number {
  const raw = snapshot.groups_json;
  if (!raw) return 0;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return Object.keys(parsed).length;
      }
    } catch {
      return 0;
    }
    return 0;
  }
  if (typeof raw === "object" && !Array.isArray(raw)) {
    return Object.keys(raw).length;
  }
  return 0;
}

const toast = useToast();
const route = useRoute();
const { sites, selectedId, setSelectedId, refresh } = useConsoleData();

const routeId = computed(() => {
  const raw = route.params.id;
  const val = Array.isArray(raw) ? raw[0] : raw;
  return val ? Number(val) : null;
});
const activeId = computed(() => routeId.value || selectedId.value);
const site = computed(
  () => sites.value.find((s) => s.id === activeId.value) || null,
);

const siteChanges = shallowRef<Change[]>([]);
const account = shallowRef<SiteAccount | null>(null);
const accountLoading = shallowRef(false);
const accountError = shallowRef<string | null>(null);
const accountFetchedAt = shallowRef<string | null>(null);
const discoveryLinks = shallowRef<SiteDiscoveryLink[]>([]);
const snapshots = shallowRef<SiteSnapshot[]>([]);

async function loadAccount() {
  if (!activeId.value) return;
  accountLoading.value = true;
  accountError.value = null;
  try {
    const resp = await api.siteAccount(activeId.value);
    account.value = resp.account || null;
    accountFetchedAt.value = resp.fetched_at || null;
    toast.success("账户额度已更新");
  } catch (err) {
    const message = errorText(err, "读取账户额度失败");
    account.value = null;
    accountError.value = message;
    toast.error(message);
  } finally {
    accountLoading.value = false;
  }
}

const regenerating = shallowRef(false);
const canRegenerateSystemToken = computed(
  () => !isSub2api.value && truthy(site.value?.system_token_fallback_enabled),
);

async function regenerateSystemToken() {
  if (!activeId.value) return;
  regenerating.value = true;
  try {
    const resp = await api.regenerateSystemToken(activeId.value);
    toast.success(resp.message || "兜底系统访问令牌已生成并保存");
    await refresh();
  } catch (err) {
    toast.error(errorText(err, "生成兜底令牌失败"));
  } finally {
    regenerating.value = false;
  }
}

// routeId → setSelectedId（路由参数驱动选中站点）
watch(
  routeId,
  (val) => {
    if (val) setSelectedId(val);
  },
  { immediate: true },
);

// 切换站点时清空账户面板，避免串号展示
watch(activeId, () => {
  account.value = null;
  accountError.value = null;
  accountFetchedAt.value = null;
  accountLoading.value = false;
  discoveryLinks.value = [];
  snapshots.value = [];
});

// 拉取该站历史变化（最近 50 条）
watch(
  activeId,
  (val, _old, onCleanup) => {
    if (!val) {
      siteChanges.value = [];
      return;
    }
    let cancelled = false;
    api
      .siteChanges(val, 50)
      .then((resp) => {
        if (!cancelled) siteChanges.value = resp.data || [];
      })
      .catch(() => {
        if (!cancelled) siteChanges.value = [];
      });
    onCleanup(() => {
      cancelled = true;
    });
  },
  { immediate: true },
);

// 拉取来源关联
watch(
  activeId,
  (val, _old, onCleanup) => {
    if (!val) return;
    let cancelled = false;
    api
      .siteDiscoveryLinks(val)
      .then((resp) => {
        if (!cancelled) discoveryLinks.value = resp.data || [];
      })
      .catch(() => {
        if (!cancelled) discoveryLinks.value = [];
      });
    onCleanup(() => {
      cancelled = true;
    });
  },
  { immediate: true },
);

// 拉取历史快照
watch(
  activeId,
  (val, _old, onCleanup) => {
    if (!val) return;
    let cancelled = false;
    api
      .siteSnapshots(val)
      .then((resp) => {
        if (!cancelled) snapshots.value = resp.data || [];
      })
      .catch(() => {
        if (!cancelled) snapshots.value = [];
      });
    onCleanup(() => {
      cancelled = true;
    });
  },
  { immediate: true },
);

const selectorValue = computed<string>({
  get: () => (site.value ? String(site.value.id) : ""),
  set: (val) => setSelectedId(val ? Number(val) : null),
});

const publicGroups = computed(() => site.value?.current_groups || {});
const loginGroups = computed(() => site.value?.current_login_groups || {});
const activeGroups = computed(() =>
  truthy(site.value?.login_enabled) && Object.keys(loginGroups.value).length
    ? loginGroups.value
    : publicGroups.value,
);
const groupRows = computed(() =>
  Object.entries(activeGroups.value)
    .map(([name, item]) => ({ name, item }))
    .sort((a, b) => a.name.localeCompare(b.name, "zh-CN")),
);
const hiddenGroupRows = computed(() =>
  Object.entries(loginGroups.value)
    .filter(([name]) => !(name in publicGroups.value))
    .map(([name, item]) => ({ name, item })),
);
const siteError = computed(() =>
  site.value?.last_error ? explainUpstreamError(site.value.last_error) : null,
);
const failures = computed(() => Number(site.value?.consecutive_failures || 0));
const isSub2api = computed(() => site.value?.platform === "sub2api");
const accountSubtitle = computed(() =>
  isSub2api.value
    ? "登录后读取 /api/v1/auth/me：余额与订阅用量"
    : "凭该账号登录态（浏览器会话 / 密码 / 令牌）读取 /api/user/self：剩余额度与用量",
);
const balanceLabel = computed(() =>
  isSub2api.value ? "账户余额" : "剩余额度",
);
const groupsPanelTitle = computed(() => {
  if (isSub2api.value) return "用户可见分组倍率";
  return truthy(site.value?.login_enabled) &&
    Object.keys(loginGroups.value).length
    ? "认证分组倍率"
    : "当前公开分组倍率";
});
const modeNote = computed(() => {
  const s = site.value;
  if (!s) return "";
  if (s.platform === "sub2api") {
    return s.auth_mode === "token"
      ? "当前渠道使用导入登录态检测该账号实际可见的分组倍率；适合开启 Turnstile 的上游。"
      : "当前渠道使用 sub2api 普通用户账号登录，检测该账号实际可见的分组倍率和用户专属倍率。";
  }
  return truthy(s.login_enabled)
    ? "当前渠道已开启认证增强监控，检测时用该账号的登录态（浏览器会话 / 密码 / 令牌）采集该账号可见的隐藏用户分组或专属分组。"
    : "当前渠道只监控公开 /api/user/groups。若该站存在特殊分组，可在编辑渠道中开启认证增强监控。";
});
const accountHint = computed(
  () => account.value?.username || account.value?.email || "",
);
const accountButtonText = computed(() =>
  accountLoading.value ? "查询中…" : account.value ? "刷新" : "查询账户额度",
);
const rpmText = computed(() => {
  const limit = account.value?.rpm_limit;
  return limit === null || limit === undefined || limit === 0
    ? "不限"
    : String(limit);
});
const requestCountText = computed(() => {
  const count = account.value?.request_count;
  return count === null || count === undefined
    ? "-"
    : Number(count).toLocaleString("zh-CN");
});
const hasSubscriptions = computed(
  () => !!account.value?.subscriptions?.length,
);
</script>

<template>
  <EmptyState
    v-if="!sites.length"
    title="还没有渠道"
    description="请先到「渠道监控」添加一个上游站点，再回来查看倍率详情。"
  />

  <div v-else-if="!site" class="flex flex-col gap-4">
    <Select v-model="selectorValue">
      <option value="">选择渠道</option>
      <option v-for="s in sites" :key="s.id" :value="String(s.id)">
        {{ s.name }}
      </option>
    </Select>
  </div>

  <div v-else class="upstream-rise flex flex-col gap-6 md:gap-8">
    <PageHeader title="渠道详情" subtitle="当前倍率、隐藏分组与历史变化">
      <template #action>
        <label class="flex items-center gap-2 text-[12.5px]">
          <span class="text-ink-muted">查看渠道</span>
          <Select v-model="selectorValue" class="w-56">
            <option v-for="s in sites" :key="s.id" :value="String(s.id)">
              {{ s.name }} · {{ platformLabel(s) }}
            </option>
          </Select>
        </label>
      </template>
    </PageHeader>

    <div class="flex flex-wrap items-start gap-4">
      <div
        class="inline-flex h-12 w-12 items-center justify-center rounded-[var(--radius-md)] bg-sunken font-serif text-[18px] font-semibold text-ink-strong ring-1 ring-line"
      >
        {{ site.name.slice(0, 1) }}
      </div>
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2">
          <h2
            class="font-serif text-[22px] font-semibold tracking-[-0.015em] text-ink-strong"
          >
            {{ site.name }}
          </h2>
          <Badge :tone="siteStatusTone(site)" dot>
            {{ siteStatusLabel(site) }}
          </Badge>
          <Badge
            v-if="sessionFailureBadge(site)"
            :tone="sessionFailureBadge(site)!.tone"
            :title="sessionFailureBadge(site)!.title"
            >{{ sessionFailureBadge(site)!.label }}</Badge
          >
          <Badge tone="neutral">{{ platformLabel(site) }}</Badge>
        </div>
        <p class="mt-1 font-mono text-[11.5px] text-ink-soft">
          {{ site.base_url }}
        </p>
      </div>
    </div>

    <div
      v-if="siteError"
      class="rounded-[var(--radius-md)] border border-danger-fg/30 bg-danger-bg px-4 py-3 text-[13px] text-danger-fg"
    >
      <div class="font-semibold">错误原因：{{ siteError.summary }}</div>
      <div class="mt-1 break-all font-mono text-[11.5px] opacity-90">
        原始错误：{{ siteError.raw }}
      </div>
    </div>

    <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard label="监控间隔" tone="info">
        <template #value>{{ site.interval_minutes }} 分</template>
        <template #icon><Timer :size="17" /></template>
      </StatCard>
      <StatCard label="公开分组" tone="brand">
        <template #value>{{ site.current_groups_count || 0 }}</template>
        <template #icon><Layers :size="17" /></template>
      </StatCard>
      <StatCard label="认证分组" tone="warning">
        <template #value>{{ site.current_login_groups_count || 0 }}</template>
        <template #icon><ShieldCheck :size="17" /></template>
      </StatCard>
      <StatCard
        label="连续失败"
        :tone="failures > 0 ? 'danger' : 'neutral'"
        :accent="failures > 0"
      >
        <template #value>{{ failures }}</template>
        <template #icon>
          <XCircle v-if="failures > 0" :size="17" />
          <TrendingDown v-else :size="17" />
        </template>
      </StatCard>
      <StatCard label="上次检测" tone="neutral">
        <template #value>{{ fmtTime(site.last_check_at) }}</template>
        <template #icon><Clock :size="17" /></template>
      </StatCard>
      <StatCard label="下次检测" tone="neutral">
        <template #value>{{ fmtTime(site.next_check_at) }}</template>
        <template #icon><Clock :size="17" /></template>
      </StatCard>
    </div>

    <div
      class="rounded-[var(--radius-md)] border border-line bg-info-bg px-4 py-3 text-[13px] leading-relaxed text-info-fg"
    >
      {{ modeNote }}
    </div>

    <Panel
      v-if="discoveryLinks.length"
      title="发现来源"
      :subtitle="`${discoveryLinks.length} 条来源关联`"
    >
      <div class="divide-y divide-line-soft">
        <div
          v-for="link in discoveryLinks"
          :key="`${link.admin_site_id}-${link.channel_id}`"
          class="grid gap-1 py-2.5 sm:grid-cols-[1fr_1fr_auto] sm:items-center"
        >
          <span class="font-semibold text-ink-strong">{{
            link.admin_site_name
          }}</span>
          <span class="text-[12px] text-ink-muted">{{
            link.channel_name || `渠道 #${link.channel_id}`
          }}</span>
          <code class="break-all font-mono text-[11px] text-ink-soft">{{
            link.upstream_base_url
          }}</code>
        </div>
      </div>
    </Panel>

    <Panel title="账户额度" :subtitle="accountSubtitle">
      <template #action>
        <div class="flex flex-col items-end gap-1.5">
          <Button
            variant="secondary"
            :loading="accountLoading"
            @click="loadAccount"
          >
            {{ accountButtonText }}
          </Button>
          <Button
            v-if="canRegenerateSystemToken"
            variant="ghost"
            size="sm"
            :loading="regenerating"
            @click="regenerateSystemToken"
          >
            {{
              site?.has_system_access_token
                ? "重新生成兜底令牌"
                : "生成兜底令牌"
            }}
          </Button>
        </div>
      </template>

      <div
        v-if="!isSub2api && truthy(site?.system_token_fallback_enabled)"
        class="mb-3 flex items-center gap-2 text-[12px] text-ink-soft"
      >
        <Badge :tone="site?.has_system_access_token ? 'success' : 'neutral'" dot>
          {{ site?.has_system_access_token ? "兜底令牌已配置" : "兜底令牌未生成" }}
        </Badge>
        <span>浏览器会话失效时自动改用兜底系统访问令牌读取数据。</span>
      </div>
      <div
        v-if="accountError"
        class="rounded-[var(--radius-md)] border border-warning-fg/30 bg-warning-bg px-4 py-3 text-[13px] text-warning-fg"
      >
        {{ accountError }}
      </div>
      <div v-else-if="account" class="flex flex-col gap-4">
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard :label="balanceLabel">
            <template #value>{{ usd(account.balance_usd) }}</template>
            <template v-if="accountHint" #hint>{{ accountHint }}</template>
          </StatCard>
          <template v-if="isSub2api">
            <StatCard label="冻结余额" :accent="false">
              <template #value>{{ usd(account.frozen_balance_usd) }}</template>
            </StatCard>
            <StatCard label="累计充值" :accent="false">
              <template #value>{{
                usd(account.total_recharged_usd)
              }}</template>
            </StatCard>
            <StatCard label="RPM 上限" :accent="false">
              <template #value>{{ rpmText }}</template>
            </StatCard>
          </template>
          <template v-else>
            <StatCard label="已用额度" :accent="false">
              <template #value>{{ usd(account.used_usd) }}</template>
            </StatCard>
            <StatCard label="请求次数" :accent="false">
              <template #value>{{ requestCountText }}</template>
            </StatCard>
            <StatCard label="用户分组" :accent="false">
              <template #value>{{ account.group || "-" }}</template>
            </StatCard>
          </template>
        </div>

        <div v-if="hasSubscriptions" class="space-y-2">
          <div class="t-micro">订阅用量</div>
          <div
            v-for="(sub, index) in account.subscriptions"
            :key="`${sub.name}-${index}`"
            class="rounded-[var(--radius-md)] border border-line bg-panel-soft px-3 py-2.5"
          >
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div class="font-semibold text-ink-strong">
                {{ sub.name || "订阅" }}
              </div>
              <div class="flex items-center gap-2">
                <Badge v-if="sub.status" tone="neutral">{{ sub.status }}</Badge>
                <span v-if="sub.expires_at" class="text-[11.5px] text-ink-soft">
                  到期 {{ fmtTime(String(sub.expires_at)) }}
                </span>
              </div>
            </div>
            <div
              class="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-muted tabular"
            >
              <span>
                日 {{ usd(sub.daily_usage_usd)
                }}{{ sub.daily_limit_usd ? ` / ${usd(sub.daily_limit_usd)}` : "" }}
              </span>
              <span>
                周 {{ usd(sub.weekly_usage_usd)
                }}{{
                  sub.weekly_limit_usd ? ` / ${usd(sub.weekly_limit_usd)}` : ""
                }}
              </span>
              <span>
                月 {{ usd(sub.monthly_usage_usd)
                }}{{
                  sub.monthly_limit_usd
                    ? ` / ${usd(sub.monthly_limit_usd)}`
                    : ""
                }}
              </span>
            </div>
          </div>
        </div>

        <div v-if="accountFetchedAt" class="text-[11.5px] text-ink-soft">
          更新于 {{ fmtTime(accountFetchedAt) }}
        </div>
      </div>
      <div v-else class="text-[13px] text-ink-muted">
        右上角点击「查询账户额度」，实时读取该渠道登录账号的余额 / 额度。
      </div>
    </Panel>

    <div
      v-if="site.login_last_error"
      class="rounded-[var(--radius-md)] border border-warning-fg/30 bg-warning-bg px-4 py-3 text-[13px] text-warning-fg"
    >
      认证错误：{{ site.login_last_error }}
    </div>

    <Panel
      v-if="hiddenGroupRows.length"
      title="认证后新增分组"
      :subtitle="`${hiddenGroupRows.length} 个隐藏分组`"
    >
      <div class="space-y-2">
        <div
          v-for="row in hiddenGroupRows"
          :key="row.name"
          class="flex items-center justify-between gap-3 rounded-[var(--radius-md)] border border-line bg-panel-soft px-3 py-2"
        >
          <div>
            <div class="font-semibold text-ink-strong">{{ row.name }}</div>
            <div class="text-[12px] text-ink-muted">
              {{ row.item.desc || "-" }}
            </div>
          </div>
          <div class="font-serif text-[15.5px] font-semibold tabular">
            {{ ratioLabel(row.item) }}
          </div>
        </div>
      </div>
    </Panel>

    <Panel :title="groupsPanelTitle" :subtitle="`${groupRows.length} 个分组`">
      <div v-if="groupRows.length" class="space-y-2">
        <div
          v-for="row in groupRows"
          :key="row.name"
          class="flex items-center justify-between gap-3 rounded-[var(--radius-md)] border border-line px-3 py-2.5 transition-colors duration-[var(--motion-fast)] hover:bg-sunken-hover"
        >
          <div class="min-w-0">
            <div class="font-semibold text-ink-strong">{{ row.name }}</div>
            <div class="truncate text-[12px] text-ink-muted">
              {{
                [
                  row.item.platform,
                  row.item.status,
                  row.item.is_exclusive ? "专属" : "",
                  row.item.desc || "-",
                ]
                  .filter(Boolean)
                  .join(" · ")
              }}
            </div>
          </div>
          <div
            class="shrink-0 font-serif text-[16px] font-semibold tabular text-ink-strong"
          >
            {{ ratioLabel(row.item) }}
          </div>
        </div>
      </div>
      <div v-else class="text-[13px] text-ink-muted">暂无倍率数据</div>
    </Panel>

    <Panel title="该站历史变化" subtitle="最近 50 条">
      <div class="mb-3 space-y-2 md:hidden">
        <div
          v-for="change in siteChanges.slice(0, 10)"
          :key="change.id"
          class="rounded-[var(--radius-md)] border border-line px-3 py-2"
        >
          <div class="flex items-center justify-between gap-2">
            <Badge :tone="changeTone(change)">
              {{ changeTypeLabel(change.change_type) }}
            </Badge>
            <time class="text-[11px] text-ink-soft">{{
              fmtTime(change.created_at)
            }}</time>
          </div>
          <div class="mt-1 font-semibold">{{ change.group_name || "-" }}</div>
          <div class="text-[12px] text-ink-muted">
            {{ changeDisplayMessage(change) }}
          </div>
        </div>
      </div>
      <div class="hidden md:block">
        <ChangeTable :changes="siteChanges" :sites="sites" :show-site="false" />
      </div>
    </Panel>

    <Panel title="历史快照" :subtitle="`${snapshots.length} 条快照`">
      <div v-if="snapshots.length" class="priceai-scrollbar overflow-x-auto pb-1">
        <table class="w-full min-w-max table-auto text-left text-[13px]">
          <thead>
            <tr
              class="border-b border-line-soft text-[11.5px] font-semibold tracking-[0.04em] text-ink-soft uppercase"
            >
              <th class="pb-2.5 pr-3">检测时间</th>
              <th class="pb-2.5 pr-3">状态</th>
              <th class="pb-2.5 pr-3">来源</th>
              <th class="pb-2.5 pr-3 text-right">分组数</th>
              <th class="pb-2.5 pr-3">错误</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="snap in snapshots"
              :key="snap.id || snap.checked_at"
              class="border-b border-line-soft transition-colors duration-[var(--motion-fast)] last:border-0 hover:bg-sunken-hover"
            >
              <td class="py-2.5 pr-3 text-ink-soft tabular">
                {{ fmtTime(snap.checked_at) }}
              </td>
              <td class="py-2.5 pr-3">
                <Badge :tone="statusTone(snap.status)" dot>
                  {{ statusLabel(snap.status) }}
                </Badge>
              </td>
              <td class="py-2.5 pr-3 text-ink-muted">
                {{ snap.source || "—" }}
              </td>
              <td class="py-2.5 pr-3 text-right tabular text-ink-strong">
                {{ snapshotGroupCount(snap) }}
              </td>
              <td class="py-2.5 pr-3 text-[12px] text-danger-fg">
                {{ snap.error_message || "" }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="text-[13px] text-ink-muted">暂无快照记录</div>
    </Panel>
  </div>
</template>
