<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import PageHeader from "@/components/PageHeader.vue";
import Panel from "@/components/Panel.vue";
import StatCard from "@/components/StatCard.vue";
import BillingAuditTrend from "@/components/BillingAuditTrend.vue";
import BillingBreakdownBars from "@/components/BillingBreakdownBars.vue";
import BillingChecksTable from "@/components/BillingChecksTable.vue";
import BillingCheckDetailDialog from "@/components/BillingCheckDetailDialog.vue";
import BillingPricesDialog from "@/components/BillingPricesDialog.vue";
import BillingSettingsDialog from "@/components/BillingSettingsDialog.vue";
import BillingUpstreamBreakdown from "@/components/BillingUpstreamBreakdown.vue";
import { Button, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import type {
  AdminSite,
  BillingAuditOverview,
  BillingRequestCheck,
  BillingTimeBucket,
  BillingUpstreamBreakdown as BillingUpstreamBreakdownRow,
} from "@/lib/types";
import { billingReasonLabel, usdPrecise } from "@/lib/format";
import { useToast } from "@/composables/useToast";

const toast = useToast();

const adminSites = ref<AdminSite[]>([]);
const overview = ref<BillingAuditOverview | null>(null);
const items = ref<BillingRequestCheck[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = 20;
const loading = ref(false);
const running = ref(false);

const adminSiteId = ref<number | null>(null);
const upstreamSiteId = ref<number | null>(null);
const status = ref<"" | BillingRequestCheck["status"]>("");
const range = ref<"all" | "1h" | "24h" | "7d" | "30d">("24h");
const keyword = ref("");
// 服务端模型过滤（辅助条 TOP5 点击）；keyword 只做当前页的本地搜索
const modelFilter = ref("");
const reasonCode = ref("");
// 趋势带点选的时段锁定（ISO 起 / 止）；设置后 range 过滤被覆盖
const pinnedBucket = ref<BillingTimeBucket | null>(null);

const showDetail = ref(false);
const detailCheck = ref<BillingRequestCheck | null>(null);
const showPrices = ref(false);
const showSettings = ref(false);

const startAt = computed(() => {
  if (pinnedBucket.value) return pinnedBucket.value.bucket_start_at;
  const rangeHours: Record<string, number | undefined> = {
    "1h": 1,
    "24h": 24,
    "7d": 24 * 7,
    "30d": 24 * 30,
  };
  const hours = rangeHours[range.value];
  if (!hours) return undefined;
  return new Date(Date.now() - hours * 3600 * 1000).toISOString();
});

// 灰卡「主因」只取灰因码（红因码与灰行无关），避免灰卡显示红因
const GRAY_REASON_CODES = new Set([
  "no_official_price",
  "no_upstream_log",
  "no_binding",
  "no_token",
  "no_log_api",
  "ratio_field_missing",
]);
const unknownTopReason = computed(() => {
  for (const item of overview.value?.reason_breakdown || []) {
    if (GRAY_REASON_CODES.has(item.code)) return item.code;
  }
  return "";
});

// 红卡「主因」对称处理：跳过灰因码取第一个红因
const mismatchTopReason = computed(() => {
  for (const item of overview.value?.reason_breakdown || []) {
    if (!GRAY_REASON_CODES.has(item.code)) return item.code;
  }
  return "";
});

// 锁定时段的终点 = 桶起点 + 锁定时记录的桶宽；未锁定时不传 end_at。
// 桶宽取点选那一刻的快照：切 range 后锁定保留，展开区间仍用旧桶宽，不会错位
const pinnedBucketMinutes = ref(5);
const endAt = computed(() => {
  if (!pinnedBucket.value) return undefined;
  const startMs = new Date(pinnedBucket.value.bucket_start_at).getTime();
  // request_at <= end_at 是闭区间，减 1 秒避免压线的行同时落进相邻两桶
  return new Date(startMs + pinnedBucketMinutes.value * 60 * 1000 - 1000).toISOString();
});

/** 桶宽随时间范围自适应，控制柱数 ≤96：1h→5min / 24h→15min / 7d→2h / 30d→8h / 全部→1d */
const bucketMinutes = computed(() => {
  const byRange: Record<string, number> = {
    "1h": 5,
    "24h": 15,
    "7d": 120,
    "30d": 480,
    all: 1440,
  };
  return byRange[range.value] ?? 5;
});

/** 活跃的聚合过滤芯片（模型 / 上游 / 原因），空数组则不渲染 */
const activeFilters = computed(() => {
  const chips: { key: string; label: string; clear: () => void }[] = [];
  if (upstreamSiteId.value !== null) {
    chips.push({ key: "upstream", label: "上游已筛选", clear: () => (upstreamSiteId.value = null) });
  }
  if (modelFilter.value) {
    chips.push({
      key: "model",
      label: `模型：${modelFilter.value}`,
      clear: () => (modelFilter.value = ""),
    });
  }
  if (reasonCode.value) {
    chips.push({
      key: "reason",
      label: `原因：${billingReasonLabel(reasonCode.value)}`,
      clear: () => (reasonCode.value = ""),
    });
  }
  return chips;
});

const filteredItems = computed(() => {
  const q = keyword.value.trim().toLowerCase();
  if (!q) return items.value;
  return items.value.filter((check) =>
    `${check.model_name} ${check.channel_name ?? ""} ${check.upstream_site_name ?? ""}`
      .toLowerCase()
      .includes(q),
  );
});

async function fetchAll(): Promise<void> {
  loading.value = true;
  try {
    const query = {
      page: page.value,
      page_size: pageSize,
      status: status.value,
      admin_site_id: adminSiteId.value,
      upstream_site_id: upstreamSiteId.value,
      model: modelFilter.value || undefined,
      reason_code: reasonCode.value || undefined,
      start_at: startAt.value,
      end_at: endAt.value,
    };
    const [overviewRes, listRes] = await Promise.all([
      api.billingOverview({ ...query, bucket_minutes: bucketMinutes.value }),
      api.billingRequests(query),
    ]);
    overview.value = overviewRes.data;
    items.value = listRes.data?.items || [];
    total.value = listRes.data?.total || 0;
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "计费核对数据读取失败");
  } finally {
    loading.value = false;
  }
}

async function runNow(): Promise<void> {
  if (running.value) return;
  running.value = true;
  try {
    const res = await api.runBillingAudit(adminSiteId.value);
    const sites = res.data?.sites || [];
    if (res.data?.executed === false) {
      if (res.data?.error) {
        toast.error(res.data.error);
      } else {
        toast.info("已有核对在跑，本轮跳过（幂等）");
      }
    } else {
      const failed = sites.filter((site) => site.error);
      if (failed.length) {
        toast.info(`核对完成，${failed.length} 个主站读取失败`);
      } else {
        toast.success("核对完成");
      }
    }
    await fetchAll();
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "触发核对失败");
    running.value = false;
  } finally {
    if (running.value) running.value = false;
  }
}

// watch 覆盖全部筛选入口；回调只改非 watched 的 page，所有函数只改 ref，
// 一律不手动 fetchAll，避免同一改动触发两次请求
watch(
  [adminSiteId, status, range, upstreamSiteId, reasonCode, modelFilter, pinnedBucket],
  () => {
    page.value = 1;
    fetchAll();
  },
);

/** 趋势带点选：锁定到那一轮的时段（记录桶宽快照） */
function selectBucket(bucket: BillingTimeBucket): void {
  pinnedBucketMinutes.value = bucketMinutes.value;
  pinnedBucket.value = bucket;
}

/** 渠道汇总行点击：明细表筛到该上游；未绑定上游的行（null）不可点 */
function selectUpstream(row: BillingUpstreamBreakdownRow): void {
  if (row.upstream_site_id === null) return;
  upstreamSiteId.value = row.upstream_site_id;
  pinnedBucket.value = null;
}

/** 辅助条：按模型筛选（服务端 model 过滤，跨页有效） */
function filterModel(modelName: string): void {
  modelFilter.value = modelName;
}

/** 辅助条 / KPI 卡：按红灰原因筛选明细 */
function filterReason(code: string): void {
  if (!code) return;
  reasonCode.value = code;
}

function clearPinned(): void {
  pinnedBucket.value = null;
}

function openDetail(check: BillingRequestCheck): void {
  // 列表行没有原始 other JSON，进详情时按 id 补拉一次
  api
    .billingCheckDetail(check.id)
    .then((res: { data: BillingRequestCheck }) => {
      detailCheck.value = res.data;
    })
    .catch(() => {
      detailCheck.value = check;
    });
  showDetail.value = true;
}

function changePage(delta: number): void {
  const next = page.value + delta;
  if (next < 1) return;
  page.value = next;
  fetchAll();
}

onMounted(async () => {
  fetchAll();
  try {
    const res = await api.adminSites();
    adminSites.value = res.data || [];
  } catch {
    adminSites.value = [];
  }
});
</script>

<template>
  <div class="upstream-rise flex flex-col gap-6 md:gap-8">
    <PageHeader
      title="计费核对"
      subtitle="主站每条消费请求 ↔ 上游实扣 ↔ 官方价格 三方对照，盯上游有没有在后台暗改倍率。"
    >
      <template #action>
        <div class="flex flex-wrap items-center gap-2">
          <Button variant="secondary" @click="showSettings = true">设置</Button>
          <Button variant="secondary" @click="showPrices = true">官方价格</Button>
          <Button variant="brand" :loading="running" title="增量拉取主站日志并逐条核对" @click="runNow">
            立即核对
          </Button>
        </div>
      </template>
    </PageHeader>

    <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      <StatCard label="核对总数" tone="neutral">
        <template #value>{{ overview?.total_count ?? "—" }}</template>
      </StatCard>
      <StatCard label="正常（绿）" tone="brand">
        <template #value>{{ overview?.ok_count ?? "—" }}</template>
      </StatCard>
      <StatCard label="异常（红）" tone="danger">
        <template #value>{{ overview?.mismatch_count ?? "—" }}</template>
        <template #hint>
          <button
            v-if="mismatchTopReason"
            type="button"
            class="text-left underline decoration-dotted underline-offset-2 hover:text-danger-fg"
            title="在明细中筛选该异常原因"
            @click="filterReason(mismatchTopReason)"
          >
            主因：{{ billingReasonLabel(mismatchTopReason) }} ›
          </button>
        </template>
      </StatCard>
      <StatCard label="无法核对（灰）" tone="warning">
        <template #value>{{ overview?.unknown_count ?? "—" }}</template>
        <template #hint>
          <button
            v-if="unknownTopReason"
            type="button"
            class="text-left underline decoration-dotted underline-offset-2 hover:text-ink-strong"
            title="在明细中筛选该原因"
            @click="filterReason(unknownTopReason)"
          >
            主因：{{ billingReasonLabel(unknownTopReason) }} ›
          </button>
        </template>
      </StatCard>
      <StatCard label="毛利合计（主站−上游）" :tone="(overview?.margin_usd_total ?? 0) < 0 ? 'danger' : 'brand'">
        <template #value>{{ usdPrecise(overview?.margin_usd_total, 4) }}</template>
        <template #hint>上游实扣 {{ usdPrecise(overview?.upstream_usd_total, 4) }}</template>
      </StatCard>
    </div>

    <Panel title="核对节奏" subtitle="每根柱 = 一轮核对，红桶出现 ▲ 表示该轮发现异常；点击柱可锁定到该轮明细">
      <BillingAuditTrend
        :buckets="overview?.time_buckets || []"
        :bucket-minutes="bucketMinutes"
        :loading="loading"
        @select="selectBucket"
      />
      <div v-if="pinnedBucket" class="mt-2 flex items-center gap-2 text-[12px] text-ink-muted">
        <span>已锁定时段：{{ pinnedBucket.bucket_start_at }} 起 {{ pinnedBucketMinutes }} 分钟</span>
        <button class="underline decoration-dotted underline-offset-2 hover:text-ink-strong" @click="clearPinned">
          取消锁定
        </button>
      </div>
    </Panel>

    <Panel title="渠道汇总" subtitle="按上游站点聚合，红率高的就是要重点盯的渠道；点行看明细">
      <BillingUpstreamBreakdown
        :rows="overview?.upstream_breakdown || []"
        :loading="loading"
        @select="selectUpstream"
      />
    </Panel>

    <Panel title="问题模型与原因">
      <BillingBreakdownBars
        :overview="overview"
        @select-model="filterModel"
        @select-reason="filterReason"
      />
    </Panel>

    <Panel
      title="请求明细"
      :subtitle="`${filteredItems.length} / ${total} 条`"
    >
      <template #action>
        <div class="flex flex-wrap gap-2">
          <Input v-model="keyword" class="w-44" type="search" placeholder="搜模型 / 渠道 / 上游" />
          <Select v-model="adminSiteId" class="w-40">
            <option :value="null">全部主站</option>
            <option v-for="site in adminSites" :key="site.id" :value="site.id">
              {{ site.name }}
            </option>
          </Select>
          <Select v-model="status" class="w-32">
            <option value="">全部状态</option>
            <option value="ok">正常</option>
            <option value="mismatch">异常</option>
            <option value="unknown">无法核对</option>
          </Select>
          <Select v-model="range" class="w-32">
            <option value="1h">近 1 小时</option>
            <option value="24h">近 24 小时</option>
            <option value="7d">近 7 天</option>
            <option value="30d">近 30 天</option>
            <option value="all">全部时间</option>
          </Select>
        </div>
      </template>

      <div class="flex flex-col gap-3">
        <div
          v-if="activeFilters.length || upstreamSiteId !== null"
          class="flex flex-wrap items-center gap-2"
        >
          <span
            v-for="chip in activeFilters"
            :key="chip.key"
            class="inline-flex items-center gap-1 rounded-full border border-line bg-panel-soft px-2.5 py-0.5 text-[11.5px] text-ink"
          >
            {{ chip.label }}
            <button type="button" class="text-ink-muted hover:text-ink-strong" :aria-label="`清除${chip.label}`" @click="chip.clear">×</button>
          </span>
        </div>
        <BillingChecksTable :items="filteredItems" :loading="loading" @open="openDetail" />
        <div v-if="total > pageSize" class="flex items-center justify-end gap-2 text-[12.5px] text-ink-muted">
          <span>第 {{ page }} 页 / 共 {{ Math.ceil(total / pageSize) }} 页</span>
          <Button variant="secondary" :disabled="page <= 1" @click="changePage(-1)">上一页</Button>
          <Button
            variant="secondary"
            :disabled="page >= Math.ceil(total / pageSize)"
            @click="changePage(1)"
          >
            下一页
          </Button>
        </div>
      </div>
    </Panel>

    <BillingCheckDetailDialog
      :open="showDetail"
      :check="detailCheck"
      @close="showDetail = false"
    />
    <BillingPricesDialog :open="showPrices" @close="showPrices = false" />
    <BillingSettingsDialog
      :open="showSettings"
      @close="(() => { showSettings = false; fetchAll(); })()"
    />
  </div>
</template>
