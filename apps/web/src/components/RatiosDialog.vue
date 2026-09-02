<script setup lang="ts">
import { computed, ref, shallowRef, watch } from "vue";
import { api } from "@/lib/api";
import {
  DEFAULT_PERF_HOURS,
  PERF_HOUR_OPTIONS,
  buildPerfMap,
  compareGroupEntries,
  summarizeNewApiGroup,
  summarizeSub2ApiGroup,
  type PerfSummaryModel,
  type PricingResponse,
} from "@/lib/perf";
import {
  fmtTime,
  groupPropertyText,
  platformLabel,
  ratioXText,
} from "@/lib/format";
import type { ModelHealth, Site } from "@/lib/types";
import { Button, Modal, Select } from "./ui";
import GroupSummaryBar from "./GroupSummaryBar.vue";
import ModelCell from "./ModelCell.vue";

interface Props {
  site: Site | null;
  open: boolean;
}
const props = defineProps<Props>();
const emit = defineEmits<{ close: [] }>();

const models = shallowRef<Record<string, ModelHealth[]> | null>(null);
const pricing = shallowRef<PricingResponse | null>(null);
const summaryModels = shallowRef<PerfSummaryModel[]>([]);
const error = ref("");
const catalogError = ref("");
const perfError = ref("");
const perfLoading = ref(false);
const perfHours = ref(DEFAULT_PERF_HOURS);
const fetchedAt = ref("");
const expanded = ref(new Set<string>());
const selectedGroup = ref("all");
const collapsedGroups = ref(new Set<string>());

function siteGroupNames(site: Site): string[] {
  const publicGroups = site.current_groups || {};
  const loginGroups = site.current_login_groups || {};
  const hasAuth = Boolean(site.login_enabled && Object.keys(loginGroups).length);
  return Object.keys(hasAuth ? loginGroups : publicGroups);
}

// --- Watch 1: fetch data on open / site / perfHours change ---
watch(
  [() => props.open, () => props.site?.id, () => perfHours.value],
  ([openVal], _old, onCleanup) => {
    if (!openVal || !props.site) {
      if (!openVal) perfLoading.value = false;
      return;
    }
    let cancelled = false;
    onCleanup(() => {
      cancelled = true;
    });

    models.value = null;
    pricing.value = null;
    summaryModels.value = [];
    error.value = "";
    catalogError.value = "";
    perfError.value = "";
    fetchedAt.value = "";
    perfLoading.value = props.site.platform === "newapi";

    if (props.site.platform === "newapi") {
      // 点击查看 = 实时穿透（refresh=1），与悬浮浮层的缓存路径区分。
      Promise.allSettled([
        api.sitePricing(props.site.id, { refresh: true }),
        api.sitePerfSummary(props.site.id, perfHours.value, { refresh: true }),
      ])
        .then(([pricingResult, summaryResult]) => {
          if (cancelled) return;
          const issues: string[] = [];
          if (pricingResult.status === "fulfilled") {
            pricing.value = pricingResult.value;
            fetchedAt.value = new Date().toISOString();
          } else {
            const message = errorText(pricingResult.reason);
            catalogError.value = message;
            issues.push(`模型清单：${message}`);
          }
          if (summaryResult.status === "fulfilled") {
            summaryModels.value = summaryResult.value.data?.models || [];
          } else {
            const message = errorText(summaryResult.reason);
            perfError.value = message;
            issues.push(`模型状态：${message}`);
          }
          error.value = issues.join("；");
        })
        .finally(() => {
          if (!cancelled) perfLoading.value = false;
        });
      return;
    }

    api
      .siteModels(props.site.id, { refresh: true })
      .then((resp) => {
        if (cancelled) return;
        models.value = resp.models_by_group || {};
        fetchedAt.value = resp.fetched_at || "";
      })
      .catch((err) => {
        if (cancelled) return;
        models.value = {};
        const message = errorText(err);
        catalogError.value = message;
        error.value = message;
      });
  },
  { immediate: true },
);

// --- Watch 2: reset selection state on open / site change ---
watch(
  [() => props.open, () => props.site?.id],
  ([openVal]) => {
    if (!openVal) return;
    // error 也在这里重置：不依赖两个 watch 的注册顺序，避免上一次
    // 打开的错误信息残留（审计 UI-010/UI-011）
    error.value = "";
    selectedGroup.value = "all";
    expanded.value = new Set();
    collapsedGroups.value = props.site
      ? new Set(siteGroupNames(props.site))
      : new Set();
  },
  { immediate: true },
);

// --- Computeds ---
const hasAuth = computed(() =>
  Boolean(
    props.site?.login_enabled &&
      Object.keys(props.site?.current_login_groups || {}).length,
  ),
);

const isNewApi = computed(() => props.site?.platform === "newapi");

const groups = computed(() => {
  if (!props.site) return [] as Array<[string, any]>;
  const publicGroups = props.site.current_groups || {};
  const loginGroups = props.site.current_login_groups || {};
  // 与 hover 浮层共用同一套排序（lib/perf.compareGroupEntries）
  return Object.entries(hasAuth.value ? loginGroups : publicGroups).sort(
    compareGroupEntries,
  );
});

const perfMap = computed(() =>
  buildPerfMap({ data: { models: summaryModels.value } }),
);

const selectedHoursLabel = computed(
  () =>
    PERF_HOUR_OPTIONS.find((option) => option.value === perfHours.value)?.label ||
    `${perfHours.value} 小时`,
);

const visibleGroups = computed(() =>
  selectedGroup.value === "all"
    ? groups.value
    : groups.value.filter(([name]) => name === selectedGroup.value),
);

const perfHoursStr = computed({
  get: () => String(perfHours.value),
  set: (val: string) => {
    perfHours.value = Number(val);
  },
});

const source = computed(() => {
  if (!props.site) return "";
  if (props.site.platform === "sub2api") return "用户可见分组";
  return hasAuth.value ? "认证可见分组" : "公开分组";
});

const statusText = computed(() => {
  if (isNewApi.value) {
    return ` · 模型清单 ${pricing.value ? "已读取" : "读取中"} · 状态范围 ${selectedHoursLabel.value}`;
  }
  if (models.value === null) return " · 正在读取上游模型";
  if (error.value) return " · 模型读取失败";
  return ` · 模型读取 ${fmtTime(fetchedAt.value)}`;
});

const rows = computed(() =>
  visibleGroups.value.map(([name, item]) => ({
    name,
    item,
    collapsed: collapsedGroups.value.has(name),
    summary: isNewApi.value
      ? summarizeNewApiGroup(name, pricing.value, perfMap.value)
      : summarizeSub2ApiGroup(name, models.value),
    // sub2api：模型健康缓存已加载且该分组无数据 → 上游不允许监控
    upstreamBlocked:
      !isNewApi.value &&
      models.value !== null &&
      !error.value &&
      !(models.value[name] || []).length,
  })),
);

// --- Template event handlers ---
function toggleGroup(name: string) {
  const next = new Set(collapsedGroups.value);
  if (next.has(name)) next.delete(name);
  else next.add(name);
  collapsedGroups.value = next;
}

function collapseAll() {
  collapsedGroups.value = new Set(visibleGroups.value.map(([name]) => name));
}

function expandAll() {
  collapsedGroups.value = new Set();
}

function errorText(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
</script>

<template>
  <Modal
    v-if="site"
    :open="open"
    wide
    :title="`${site.name} 分组倍率`"
    :subtitle="`${platformLabel(site)} · ${site.base_url}`"
    @close="emit('close')"
  >
    <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div class="text-[12.5px] text-ink-muted">
        {{ source }} · {{ groups.length }} 个分组 · 上次检测 {{ fmtTime(site?.last_check_at) }}{{ statusText }}
      </div>
      <label v-if="isNewApi" class="flex items-center gap-2 text-[12.5px] text-ink-muted">
        <span>状态时间</span>
        <Select v-model="perfHoursStr" class="w-28 py-1 text-[12.5px]">
          <option
            v-for="option in PERF_HOUR_OPTIONS"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </Select>
      </label>
      <div class="flex items-center gap-1">
        <Button variant="ghost" class="px-2 py-1 text-[12.5px]" @click="collapseAll">
          全部折叠
        </Button>
        <Button variant="ghost" class="px-2 py-1 text-[12.5px]" @click="expandAll">
          全部展开
        </Button>
      </div>
      <label class="flex items-center gap-1.5 text-[12.5px] font-semibold text-ink-strong">
        <span>分组</span>
        <Select
          v-model="selectedGroup"
          class="w-32 border-[var(--color-accent)] bg-sunken py-1 text-[12.5px] font-semibold"
        >
          <option value="all">全部分组</option>
          <option v-for="[name] in groups" :key="name" :value="name">
            {{ name }}
          </option>
        </Select>
      </label>
    </div>

    <div
      v-if="isNewApi"
      class="mb-3 rounded-xl border border-line bg-info-bg px-3 py-2 text-[12.5px] text-info-fg"
    >
      模型清单按当前分组归属展示；状态来自上游
      <code>perf-metrics/summary</code>，是模型级汇总，不代表单独某个分组的状态。
      分组默认收起，点击分组左侧&ldquo;展开&rdquo;查看模型。
      <template v-if="summaryModels.length"> 已获取 {{ summaryModels.length }} 个模型的状态。</template>
    </div>

    <div class="priceai-scrollbar overflow-x-auto pb-1">
      <table class="w-full min-w-max table-auto text-left text-sm">
        <thead>
          <tr class="border-b border-line-soft text-[12.5px] font-semibold text-ink-muted">
            <th class="pb-2">分组</th>
            <th class="pb-2">倍率</th>
            <th class="pb-2">模型状态 / 倍率</th>
            <th class="pb-2">属性</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!rows.length">
            <td colspan="4" class="py-8 text-center text-ink-muted">
              暂无倍率数据
            </td>
          </tr>
          <template v-else>
            <tr
              v-for="row in rows"
              :key="row.name"
              class="border-b border-line-soft align-top last:border-0"
            >
              <td class="py-3 pr-3 font-bold text-ink-strong">
                <button
                  type="button"
                  class="flex items-center gap-2 rounded-[var(--radius-md)] px-1.5 py-1 text-left hover:bg-sunken-hover hover:text-accent"
                  :aria-expanded="!row.collapsed"
                  @click="toggleGroup(row.name)"
                >
                  <span class="inline-flex h-5 min-w-5 items-center justify-center rounded-[var(--radius-sm)] bg-sunken px-1 text-[10px] font-semibold text-ink-muted">
                    {{ row.collapsed ? "展开" : "收起" }}
                  </span>
                  <span>{{ row.name }}</span>
                </button>
              </td>
              <td class="py-3 pr-3 font-extrabold tabular-nums text-ink-strong">
                {{ ratioXText(row.item) }}
              </td>
              <td class="py-3 pr-3">
                <span
                  v-if="row.upstreamBlocked"
                  class="text-[12.5px] font-semibold text-warning-fg"
                >上游不允许监控</span>
                <template v-else>
                  <GroupSummaryBar :summary="row.summary" :collapsed="row.collapsed" />
                  <ModelCell
                    v-if="!row.collapsed"
                    v-model:expanded="expanded"
                    :site-id="site!.id"
                    :group-name="row.name"
                    :models="models"
                    :pricing="pricing"
                    :perf-map="perfMap"
                    :is-new-api="isNewApi"
                    :perf-loading="perfLoading"
                    :error="isNewApi ? catalogError : error"
                  />
                </template>
              </td>
              <td class="py-3 text-[12.5px] text-ink-muted">
                {{ groupPropertyText(row.item || {}) }}
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <div
      v-if="error || perfError"
      class="mt-3 rounded-xl bg-danger-bg px-3 py-2 text-[12.5px] text-danger-fg"
    >
      {{ error || `模型状态：${perfError}` }}
    </div>
  </Modal>
</template>
