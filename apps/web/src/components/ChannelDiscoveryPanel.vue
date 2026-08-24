<script setup lang="ts">
import { computed, ref, shallowRef, watch } from "vue";
import { Check, Link2, RefreshCw, Search } from "lucide-vue-next";
import { api } from "@/lib/api";
import { errorText, useToast } from "@/composables/useToast";
import type {
  AdminSite,
  ChannelDiscoveryCandidate,
  ChannelDiscoveryImportResult,
} from "@/lib/types";
import Badge from "@/components/Badge.vue";
import { Button, Input, Select, Spinner } from "@/components/ui";

type RowState = {
  status: string;
  siteId?: number | null;
  message?: string;
  authMode?: string | null;
  canSync?: boolean;
};

function candidateKey(candidate: ChannelDiscoveryCandidate): string {
  return candidate.base_url;
}

function sessionLabel(status: string): string {
  const labels: Record<string, string> = {
    existing: "已监控",
    created: "已创建，待配置登录",
  };
  return labels[status] || status || "待处理";
}

function sessionTone(
  status: string,
): "neutral" | "success" | "warning" | "danger" | "info" {
  if (status === "existing") return "success";
  if (status === "created") return "info";
  if (status === "failed" || status === "conflict" || status === "invalid")
    return "danger";
  return "neutral";
}

function initialRowState(candidate: ChannelDiscoveryCandidate): RowState {
  if (candidate.existing_site_id) {
    return {
      status: "existing",
      siteId: candidate.existing_site_id,
      authMode: "token",
      canSync: false,
    };
  }
  return { status: "" };
}

interface Props {
  open: boolean;
  onImported: () => Promise<void> | void;
}
const props = defineProps<Props>();
const emit = defineEmits<{
  close: [];
  editSite: [siteId: number];
}>();

const toast = useToast();
const adminSites = shallowRef<AdminSite[]>([]);
const adminSiteId = ref<number | null>(null);
const candidates = shallowRef<ChannelDiscoveryCandidate[]>([]);
const selected = ref<Set<string>>(new Set());
const rowStates = shallowRef<Record<string, RowState>>({});
const keyword = ref("");
const loading = ref(false);
const loadingSites = ref(false);
const busy = ref(false);
const message = ref("");
const intervalMinutes = ref(3);
const progress = ref<{
  current: number;
  total: number;
  baseUrl: string;
} | null>(null);

async function loadAdminSites() {
  loadingSites.value = true;
  try {
    const response = await api.adminSites();
    const newApiSites = (response.data || []).filter(
      (site) => site.platform === "newapi",
    );
    adminSites.value = newApiSites;
    adminSiteId.value =
      adminSiteId.value != null &&
      newApiSites.some((site) => site.id === adminSiteId.value)
        ? adminSiteId.value
        : (newApiSites[0]?.id ?? null);
    message.value = newApiSites.length ? "" : "暂无可用的 NewAPI 主站";
  } catch (err) {
    adminSites.value = [];
    adminSiteId.value = null;
    message.value = errorText(err, "主站列表加载失败");
  } finally {
    loadingSites.value = false;
  }
}

async function loadCandidates() {
  if (adminSiteId.value == null) return;
  loading.value = true;
  message.value = "";
  try {
    const response = await api.channelCandidates(adminSiteId.value);
    const next = response.data || [];
    candidates.value = next;
    const valid = new Set(next.map(candidateKey));
    selected.value = new Set(
      [...selected.value].filter((key) => valid.has(key)),
    );
    const nextStates: Record<string, RowState> = {};
    for (const candidate of next) {
      nextStates[candidateKey(candidate)] =
        rowStates.value[candidateKey(candidate)] ||
        initialRowState(candidate);
    }
    rowStates.value = nextStates;
  } catch (err) {
    candidates.value = [];
    selected.value = new Set();
    message.value = errorText(err, "候选渠道加载失败");
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.open,
  () => {
    if (props.open) void loadAdminSites();
  },
  { immediate: true },
);

watch(
  () => [props.open, adminSiteId.value] as const,
  () => {
    if (props.open && adminSiteId.value != null) void loadCandidates();
  },
  { immediate: true },
);

const adminSiteIdModel = computed<string>({
  get: () => (adminSiteId.value != null ? String(adminSiteId.value) : ""),
  set: (v: string) => {
    adminSiteId.value = v ? Number(v) || null : null;
  },
});

const intervalMinutesModel = computed<string>({
  get: () => String(intervalMinutes.value),
  set: (v: string) => {
    const parsed = Number(v);
    if (!Number.isFinite(parsed)) return;
    intervalMinutes.value = Math.min(1440, Math.max(1, Math.trunc(parsed)));
  },
});

const filteredCandidates = computed(() => {
  const query = keyword.value.trim().toLowerCase();
  if (!query) return candidates.value;
  return candidates.value.filter((candidate) =>
    `${candidate.name} ${candidate.base_url} ${candidate.channel_names.join(" ")}`
      .toLowerCase()
      .includes(query),
  );
});

const selectedCandidates = computed(() =>
  candidates.value.filter((candidate) =>
    selected.value.has(candidateKey(candidate)),
  ),
);

const stats = computed(() => {
  const existing = candidates.value.filter(
    (candidate) => candidate.existing_site_id,
  ).length;
  const pending = candidates.value.length - existing;
  const waiting = candidates.value.filter((candidate) => {
    const status = rowStates.value[candidateKey(candidate)]?.status;
    return [
      "no_session",
      "expired",
      "permission_required",
      "extension_unavailable",
      "failed",
    ].includes(status || "");
  }).length;
  return { total: candidates.value.length, existing, pending, waiting };
});

const statItems = computed(() => [
  { label: "发现", value: stats.value.total },
  { label: "已监控", value: stats.value.existing },
  { label: "待添加", value: stats.value.pending },
  { label: "待处理", value: stats.value.waiting },
]);

interface RowWithData {
  candidate: ChannelDiscoveryCandidate;
  key: string;
  state: RowState;
}

const rowsWithState = computed<RowWithData[]>(() =>
  filteredCandidates.value.map((candidate) => ({
    candidate,
    key: candidateKey(candidate),
    state:
      rowStates.value[candidateKey(candidate)] ||
      initialRowState(candidate),
  })),
);

function toggleCandidate(candidate: ChannelDiscoveryCandidate) {
  const key = candidateKey(candidate);
  const next = new Set(selected.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  selected.value = next;
}

function toggleAllVisible() {
  const next = new Set(selected.value);
  const allSelected = filteredCandidates.value.every((candidate) =>
    next.has(candidateKey(candidate)),
  );
  for (const candidate of filteredCandidates.value) {
    const key = candidateKey(candidate);
    if (allSelected) next.delete(key);
    else next.add(key);
  }
  selected.value = next;
}

function setRowState(key: string, state: RowState) {
  rowStates.value = { ...rowStates.value, [key]: state };
}

function applyImportedRow(
  candidate: ChannelDiscoveryCandidate,
  result: ChannelDiscoveryImportResult,
) {
  const key = candidateKey(candidate);
  if (!result.site_id) {
    setRowState(key, {
      status: result.status || "failed",
      message: result.message || "导入失败",
      authMode: candidate.existing_site_auth_mode || null,
      canSync: false,
    });
    return;
  }
  setRowState(key, {
    status: result.status === "created" ? "created" : "existing",
    siteId: result.site_id,
    authMode: "token",
    canSync: false,
    message: result.message || undefined,
  });
}

async function importSelected() {
  if (adminSiteId.value == null || !selectedCandidates.value.length || busy.value)
    return;
  busy.value = true;
  message.value = "";
  try {
    const response = await api.importDiscoveredSites({
      admin_site_id: adminSiteId.value,
      interval_minutes: intervalMinutes.value,
      items: selectedCandidates.value.map((candidate) => ({
        base_url: candidate.base_url,
        name: candidate.name,
        channel_ids: candidate.channel_ids,
        channel_names: candidate.channel_names,
      })),
    });
    const results = response.data || [];
    const resultByUrl = new Map(
      results.map((result) => [result.base_url, result]),
    );
    let index = 0;
    for (const candidate of selectedCandidates.value) {
      progress.value = {
        current: index + 1,
        total: selectedCandidates.value.length,
        baseUrl: candidate.base_url,
      };
      index++;
      const result = resultByUrl.get(candidate.base_url);
      if (!result) {
        setRowState(candidateKey(candidate), {
          status: "failed",
          authMode: "token",
          canSync: false,
          message: "后端未返回该候选结果",
        });
        continue;
      }
      applyImportedRow(candidate, result);
    }
    selected.value = new Set();
    await props.onImported();
    const failed = results.filter((result) =>
      ["invalid", "conflict", "failed"].includes(result.status),
    ).length;
    if (failed) {
      toast.info(
        `已处理 ${results.length - failed} 个候选，${failed} 个需要处理`,
      );
    } else {
      toast.success(`已处理 ${results.length} 个候选渠道`);
    }
  } catch (err) {
    const text = errorText(err, "批量导入失败");
    message.value = text;
    toast.error(text);
  } finally {
    progress.value = null;
    busy.value = false;
  }
}
</script>

<template>
  <div v-if="open" class="flex flex-col gap-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex min-w-0 items-center gap-2">
        <Link2 :size="15" class="shrink-0 text-accent" aria-hidden="true" />
        <span class="text-sm font-semibold text-ink-strong">
          从主站发现 · NewAPI
        </span>
        <Badge tone="info">只读发现</Badge>
      </div>
      <Button
        variant="secondary"
        size="sm"
        class="h-8"
        :loading="loading || loadingSites"
        :disabled="adminSiteId == null"
        aria-label="刷新候选渠道"
        @click="loadCandidates"
      >
        <RefreshCw v-if="!loading && !loadingSites" :size="13" />
        刷新
      </Button>
    </div>

    <div class="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <label class="block space-y-1.5">
        <span class="text-[12.5px] font-semibold text-ink-muted">来源主站</span>
        <Select
          v-model="adminSiteIdModel"
          :disabled="loadingSites || !adminSites.length"
          aria-label="来源主站"
        >
          <option v-if="!adminSites.length" value="">暂无 NewAPI 主站</option>
          <option v-for="site in adminSites" :key="site.id" :value="site.id">
            {{ site.name }} · {{ site.base_url }}
          </option>
        </Select>
      </label>
      <label class="block space-y-1.5">
        <span class="text-[12.5px] font-semibold text-ink-muted">筛选候选</span>
        <div class="relative">
          <Search
            :size="14"
            class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-soft"
            aria-hidden="true"
          />
          <Input
            v-model="keyword"
            class="pl-9"
            placeholder="名称或 Base URL"
            aria-label="筛选候选"
          />
        </div>
      </label>
      <label class="block space-y-1.5 sm:col-span-2">
        <span class="text-[12.5px] font-semibold text-ink-muted">
          新建渠道监控间隔（分钟）
        </span>
        <div class="flex flex-wrap items-center gap-2">
          <Input
            v-model="intervalMinutesModel"
            class="w-32"
            type="number"
            min="1"
            max="1440"
            aria-label="新建渠道监控间隔"
          />
          <span class="text-[11px] text-ink-soft">
            新建站点使用此间隔，已存在站点保留原配置
          </span>
        </div>
      </label>
    </div>

    <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <div
        v-for="item in statItems"
        :key="item.label"
        class="rounded-xl border border-line bg-panel-soft px-3 py-2"
      >
        <div class="text-[11px] text-ink-muted">{{ item.label }}</div>
        <div class="mt-0.5 text-xl font-extrabold tabular-nums text-ink-strong">
          {{ item.value }}
        </div>
      </div>
    </div>

    <div
      v-if="message"
      class="rounded-[var(--radius-md)] border border-[var(--color-warning-fg)]/25 bg-warning-bg px-3 py-2 text-[12.5px] text-warning-fg"
    >
      {{ message }}
    </div>

    <div
      v-if="progress"
      class="flex min-w-0 items-center gap-3 rounded-[var(--radius-md)] border border-[var(--color-accent)]/25 bg-success-bg px-3 py-2 text-[12.5px] text-success-fg"
    >
      <Spinner />
      <span class="min-w-0 truncate">
        正在创建监控渠道 {{ progress.current }}/{{ progress.total }} · {{ progress.baseUrl }}
      </span>
    </div>

    <div class="priceai-scrollbar hidden min-w-0 overflow-x-auto rounded-xl border border-line-soft sm:block">
      <table class="w-full min-w-[760px] table-fixed text-left text-sm">
        <colgroup>
          <col class="w-10" />
          <col class="w-[22%]" />
          <col class="w-[34%]" />
          <col class="w-[18%]" />
          <col class="w-[20%]" />
        </colgroup>
        <thead class="bg-panel-soft text-[12.5px] font-semibold text-ink-muted">
          <tr class="border-b border-line-soft">
            <th class="px-3 py-2.5">
              <input
                type="checkbox"
                :checked="filteredCandidates.length > 0 && filteredCandidates.every((candidate) => selected.has(candidateKey(candidate)))"
                @change="toggleAllVisible"
                aria-label="全选候选渠道"
              />
            </th>
            <th class="px-3 py-2.5">来源渠道</th>
            <th class="px-3 py-2.5">Base URL</th>
            <th class="px-3 py-2.5">监控状态</th>
            <th class="px-3 py-2.5">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="5" class="px-3 py-12 text-center text-sm text-ink-muted">
              <span class="inline-flex items-center gap-2"><Spinner /> 正在读取主站渠道</span>
            </td>
          </tr>
          <tr v-else-if="!filteredCandidates.length">
            <td colspan="5" class="px-3 py-12 text-center text-sm text-ink-muted">
              暂无匹配候选
            </td>
          </tr>
          <template v-else>
            <tr
              v-for="row in rowsWithState"
              :key="row.key"
              class="border-b border-line-soft last:border-0 hover:bg-sunken-hover"
            >
              <td class="px-3 py-3 align-top">
                <input
                  type="checkbox"
                  :checked="selected.has(row.key)"
                  @change="toggleCandidate(row.candidate)"
                  :aria-label="`选择 ${row.candidate.name}`"
                />
              </td>
              <td class="max-w-0 px-3 py-3 align-top">
                <div class="truncate font-semibold text-ink-strong">
                  {{ row.candidate.name }}
                </div>
                <div
                  class="mt-1 truncate text-[11px] text-ink-soft"
                  :title="row.candidate.channel_names.join('、')"
                >
                  {{ row.candidate.channel_count }} 个主站渠道
                </div>
              </td>
              <td class="max-w-0 px-3 py-3 align-top">
                <code
                  class="block truncate text-[12.5px] text-ink"
                  :title="row.candidate.base_url"
                >
                  {{ row.candidate.base_url }}
                </code>
              </td>
              <td class="px-3 py-3 align-top">
                <Badge :tone="sessionTone(row.state.status)" dot>
                  {{ row.state.status ? sessionLabel(row.state.status) : (row.candidate.existing_site_id ? '已监控' : '待添加') }}
                </Badge>
                <div
                  v-if="row.state.message"
                  class="mt-1 max-w-[180px] truncate text-[10px] text-danger-fg"
                  :title="row.state.message"
                >
                  {{ row.state.message }}
                </div>
              </td>
              <td class="px-3 py-3 align-top">
                <div class="flex flex-wrap gap-1.5">
                  <Button
                    v-if="row.state.siteId"
                    variant="ghost"
                    size="sm"
                    class="h-8"
                    aria-label="编辑认证"
                    @click="emit('editSite', Number(row.state.siteId))"
                  >
                    编辑认证
                  </Button>
                  <Check
                    v-if="row.state.siteId"
                    :size="16"
                    class="mt-1 text-accent"
                    aria-label="已创建"
                  />
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <div class="space-y-2 sm:hidden">
      <div
        v-if="loading"
        class="rounded-xl border border-line-soft px-3 py-10 text-center text-sm text-ink-muted"
      >
        <span class="inline-flex items-center gap-2"><Spinner /> 正在读取主站渠道</span>
      </div>
      <div
        v-else-if="!filteredCandidates.length"
        class="rounded-xl border border-line-soft px-3 py-10 text-center text-sm text-ink-muted"
      >
        暂无匹配候选
      </div>
      <template v-else>
        <div
          v-for="row in rowsWithState"
          :key="`mobile-${row.key}`"
          class="space-y-2 rounded-xl border border-line-soft bg-panel-soft p-3"
        >
          <div class="flex min-w-0 items-start gap-2">
            <input
              type="checkbox"
              class="mt-1 shrink-0"
              :checked="selected.has(row.key)"
              @change="toggleCandidate(row.candidate)"
              :aria-label="`选择 ${row.candidate.name}`"
            />
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-start justify-between gap-2">
                <div class="min-w-0">
                  <div class="break-words font-semibold text-ink-strong">
                    {{ row.candidate.name }}
                  </div>
                  <div class="mt-0.5 text-[11px] text-ink-soft">
                    {{ row.candidate.channel_count }} 个主站渠道
                  </div>
                </div>
                <Badge :tone="sessionTone(row.state.status)" dot>
                  {{ row.state.status ? sessionLabel(row.state.status) : (row.candidate.existing_site_id ? '已监控' : '待添加') }}
                </Badge>
              </div>
              <code class="mt-2 block break-all text-[11px] leading-5 text-ink">
                {{ row.candidate.base_url }}
              </code>
              <div
                v-if="row.state.message"
                class="mt-1 break-words text-[10px] text-danger-fg"
              >
                {{ row.state.message }}
              </div>
            </div>
          </div>
          <div v-if="row.state.siteId" class="flex flex-wrap gap-1.5 pl-6">
            <Button
              v-if="row.state.siteId"
              variant="ghost"
              size="sm"
              class="h-8"
              aria-label="编辑认证"
              @click="emit('editSite', Number(row.state.siteId))"
            >
              编辑认证
            </Button>
          </div>
        </div>
      </template>
    </div>

    <div class="flex flex-wrap items-center justify-between gap-3 border-t border-line-soft pt-3">
      <span class="text-[12.5px] text-ink-muted">
        已选择 <b class="tabular-nums text-ink-strong">{{ selectedCandidates.length }}</b> 个候选
      </span>
      <div class="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          size="sm"
          class="h-8"
          :disabled="busy"
          @click="emit('close')"
        >
          返回手动添加
        </Button>
        <Button
          variant="brand"
          size="sm"
          class="h-8"
          :loading="busy"
          :disabled="!selectedCandidates.length || adminSiteId == null"
          @click="importSelected"
        >
          <Link2 v-if="!busy" :size="13" />
          添加渠道
        </Button>
      </div>
    </div>
  </div>
</template>
