<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from "vue";
import { RouterLink } from "vue-router";
import { Activity, AlertTriangle, CheckCircle2, PauseCircle } from "lucide-vue-next";
import Panel from "@/components/Panel.vue";
import StatCard from "@/components/StatCard.vue";
import { Button } from "@/components/ui";
import { errorText, useToast } from "@/composables/useToast";
import { api } from "@/lib/api";
import {
  retainLastSuccessfulMainSiteChannels,
  type MainSiteChannels,
} from "@/lib/mainSiteHealth";
import { normalizedChannelStatus } from "@/lib/sub2apiChannel";
import type { Channel } from "@/lib/types";

/**
 * 主站健康总览：聚合所有 NewAPI / sub2api 主站下渠道的状态。
 * 只在挂载时拉一次（不跟随总览页 15s 轮询），避免频繁打上游管理接口。
 */
const toast = useToast();
const rows = shallowRef<MainSiteChannels[]>([]);
const loading = ref(true);
const error = ref("");
const busyKey = ref<string | null>(null);
// 首次自动加载不弹提示（避免进总览就冒泡），只有用户点「刷新」才反馈
let notify = false;

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const resp = await api.adminSites();
    const sites = resp.data || [];
    if (!sites.length) {
      rows.value = [];
      return;
    }
    const loaded = await Promise.all(
      sites.map(async (site) => {
        try {
          const chResp = await api.channels(site.id);
          return { site, channels: chResp.data || [] };
        } catch (err) {
          return {
            site,
            channels: [],
            error: err instanceof Error ? err.message : String(err),
          };
        }
      }),
    );
    rows.value = retainLastSuccessfulMainSiteChannels(rows.value, loaded);
    const failed = loaded.filter((r) => r.error);
    if (notify) {
      if (failed.length) {
        toast.error(`${failed.length} 个主站的渠道读取失败：${failed[0].error}`);
      } else {
        const total = loaded.reduce((n, r) => n + r.channels.length, 0);
        toast.success(`已刷新：${loaded.length} 个主站 · ${total} 个渠道`);
      }
    }
  } catch (err) {
    const message = errorText(err, "读取主站失败");
    error.value = message;
    if (notify) toast.error(message);
  } finally {
    loading.value = false;
  }
}

onMounted(load);

const stats = computed(() => {
  const acc = { total: 0, active: 0, disabled: 0, error: 0 };
  for (const row of rows.value) {
    for (const ch of row.channels) {
      acc.total += 1;
      const status = normalizedChannelStatus(ch);
      if (status === "active") acc.active += 1;
      else if (status === "disabled") acc.disabled += 1;
      else acc.error += 1;
    }
  }
  return acc;
});

const recoverableNewApiErrors = computed(() =>
  rows.value.flatMap((row) =>
    row.site.platform === "newapi"
      ? row.channels
          .filter((ch) => Number(ch.status) === 3)
          .map((ch) => ({ ch, site: row.site }))
      : [],
  ),
);

const failedSites = computed(() => rows.value.filter((r) => r.error));

const siteCountLabel = computed(() =>
  rows.value.length ? `${rows.value.length} 个主站` : "未配置主站",
);

async function reEnable(siteId: number, ch: Channel) {
  const label = ch.name || `#${ch.id}`;
  const actionKey = `${siteId}:${ch.id}`;
  busyKey.value = actionKey;
  try {
    const resp = await api.updateChannel(siteId, ch.id, { status: 1 });
    if (!resp.success) throw new Error(resp.message || "重新启用失败");
    await load();
    toast.success(`已重新启用渠道「${label}」`);
  } catch (err) {
    const message = errorText(err, `重新启用「${label}」失败`);
    error.value = message;
    toast.error(message);
  } finally {
    if (busyKey.value === actionKey) busyKey.value = null;
  }
}

function refresh() {
  notify = true;
  load();
}
</script>

<template>
  <Panel title="主站健康" :subtitle="`NewAPI / sub2api 主站渠道状态 · ${siteCountLabel}`">
    <template #action>
      <div class="flex items-center gap-2">
        <Button variant="secondary" :loading="loading" @click="refresh">
          刷新
        </Button>
        <RouterLink
          to="/channels"
          class="rounded-[var(--radius-sm)] px-2.5 py-1 text-[13px] font-medium text-accent transition-colors duration-[var(--motion-fast)] hover:text-accent-hover hover:underline"
        >
          去管理 →
        </RouterLink>
      </div>
    </template>

    <div
      v-if="error"
      class="mb-3 rounded-[var(--radius-sm)] border border-danger-fg/30 bg-danger-bg px-3 py-2 text-[13px] text-danger-fg"
    >
      {{ error }}
    </div>

    <div v-if="!loading && !rows.length" class="py-8 text-center text-[13px] text-ink-muted">
      还没有配置主站。到「
      <RouterLink to="/channels" class="text-accent hover:underline">主站监控</RouterLink>
      」添加 NewAPI 或 sub2api 主站。
    </div>
    <template v-else>
      <div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="渠道总数" tone="brand">
          <template #value>{{ stats.total }}</template>
          <template #hint>{{ siteCountLabel }}</template>
          <template #icon><Activity :size="17" /></template>
        </StatCard>
        <StatCard label="运行中" tone="info">
          <template #value>{{ stats.active }}</template>
          <template #hint>状态正常的渠道</template>
          <template #icon><CheckCircle2 :size="17" /></template>
        </StatCard>
        <StatCard label="已停用" tone="warning">
          <template #value>{{ stats.disabled }}</template>
          <template #hint>主站中已关闭</template>
          <template #icon><PauseCircle :size="17" /></template>
        </StatCard>
        <StatCard label="异常" :tone="stats.error ? 'danger' : 'neutral'">
          <template #value>{{ stats.error }}</template>
          <template #hint>{{ stats.error ? '需要检查渠道配置' : '无异常渠道' }}</template>
          <template #icon><AlertTriangle :size="17" /></template>
        </StatCard>
      </div>

      <div
        v-if="recoverableNewApiErrors.length"
        class="mt-3 rounded-[var(--radius-md)] border border-danger-fg/30 bg-danger-bg px-4 py-3 text-[13px]"
      >
        <div class="mb-1.5 flex items-center gap-1.5 font-semibold text-danger-fg">
          <AlertTriangle :size="13" />
          {{ recoverableNewApiErrors.length }} 个 NewAPI 渠道被自动停用，请排查
        </div>
        <div class="flex flex-wrap gap-1.5">
          <span
            v-for="{ ch, site } in recoverableNewApiErrors"
            :key="site.id + '-' + ch.id"
            class="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-line bg-panel px-2 py-1 text-[12px]"
          >
            <span class="font-semibold text-ink-strong">{{ ch.name || ('#' + ch.id) }}</span>
            <span v-if="rows.length > 1" class="text-ink-soft">{{ site.name }}</span>
            <button
              class="text-accent transition-opacity duration-[var(--motion-fast)] hover:opacity-80 disabled:opacity-50"
              :disabled="busyKey === site.id + ':' + ch.id"
              @click="reEnable(site.id, ch)"
            >
              重新启用
            </button>
          </span>
        </div>
      </div>

      <div
        v-if="failedSites.length"
        class="mt-3 rounded-[var(--radius-md)] border border-warning-fg/25 bg-warning-bg px-4 py-3 text-[12.5px] text-warning-fg"
      >
        <div v-for="r in failedSites" :key="r.site.id">
          主站「{{ r.site.name }}」渠道读取失败：{{ r.error }}
        </div>
      </div>
    </template>
  </Panel>
</template>
