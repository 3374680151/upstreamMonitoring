<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import PageHeader from "@/components/PageHeader.vue";
import Panel from "@/components/Panel.vue";
import StatCard from "@/components/StatCard.vue";
import BillingChecksTable from "@/components/BillingChecksTable.vue";
import BillingCheckDetailDialog from "@/components/BillingCheckDetailDialog.vue";
import BillingPricesDialog from "@/components/BillingPricesDialog.vue";
import BillingSettingsDialog from "@/components/BillingSettingsDialog.vue";
import { Button, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import type {
  AdminSite,
  BillingAuditOverview,
  BillingRequestCheck,
} from "@/lib/types";
import { usdPrecise } from "@/lib/format";
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
const status = ref<"" | BillingRequestCheck["status"]>("");
const range = ref<"all" | "1h" | "24h" | "7d" | "30d">("24h");
const keyword = ref("");

const showDetail = ref(false);
const detailCheck = ref<BillingRequestCheck | null>(null);
const showPrices = ref(false);
const showSettings = ref(false);

const startAt = computed(() => {
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
      start_at: startAt.value,
    };
    const [overviewRes, listRes] = await Promise.all([
      api.billingOverview(query),
      api.billingRequests(query),
    ]);
    overview.value = overviewRes.data;
    items.value = listRes.data?.items || [];
    total.value = listRes.data?.total || 0;
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
      toast.info("已有核对在跑，本轮跳过（幂等）");
    } else {
      const failed = sites.filter((site) => site.error);
      if (failed.length) {
        toast.info(`核对完成，${failed.length} 个主站读取失败`);
      } else {
        toast.success("核对完成");
      }
    }
    await fetchAll();
  } finally {
    running.value = false;
  }
}

watch([adminSiteId, status, range], () => {
  page.value = 1;
  fetchAll();
});

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
        <template #hint>{{ overview?.reason_breakdown?.[0] ? `主因：${overview.reason_breakdown[0].code}` : " " }}</template>
      </StatCard>
      <StatCard label="无法核对（灰）" tone="warning">
        <template #value>{{ overview?.unknown_count ?? "—" }}</template>
      </StatCard>
      <StatCard label="毛利合计（主站−上游）" :tone="(overview?.margin_usd_total ?? 0) < 0 ? 'danger' : 'brand'">
        <template #value>{{ usdPrecise(overview?.margin_usd_total, 4) }}</template>
        <template #hint>上游实扣 {{ usdPrecise(overview?.upstream_usd_total, 4) }}</template>
      </StatCard>
    </div>

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
