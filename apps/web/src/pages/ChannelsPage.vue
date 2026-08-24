<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from "vue";
import { Cloud, KeyRound, RefreshCw, Settings2 } from "lucide-vue-next";
import AdminSiteFormDialog from "@/components/AdminSiteFormDialog.vue";
import Badge from "@/components/Badge.vue";
import ChannelFormDialog from "@/components/ChannelFormDialog.vue";
import ChannelPriorityDialog from "@/components/ChannelPriorityDialog.vue";
import PageHeader from "@/components/PageHeader.vue";
import Panel from "@/components/Panel.vue";
import Sub2ApiChannelDialog from "@/components/Sub2ApiChannelDialog.vue";
import Sub2ApiChannelTable from "@/components/Sub2ApiChannelTable.vue";
import { Button, Input, Select, Spinner } from "@/components/ui";
import { claimAutomaticRefresh } from "@/lib/automaticRefresh";
import { api } from "@/lib/api";
import { normalizedChannelStatus } from "@/lib/sub2apiChannel";
import { explainUpstreamError } from "@/lib/upstreamError";
import { errorText, useToast } from "@/composables/useToast";
import { useAppActions } from "@/composables/useAppActions";
import { useReconcileMode, type ReconcileMode } from "@/composables/useReconcileMode";
import type {
  AdminSite,
  Channel,
  ChannelUpstreamBinding,
  ChannelUpstreamBindingPayload,
  GroupItem,
  Sub2ApiGroupRef,
} from "@/lib/types";

type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info";

const STATUS_META: Record<number, { label: string; tone: BadgeTone }> = {
  1: { label: "启用", tone: "success" },
  2: { label: "手动停用", tone: "warning" },
  3: { label: "自动停用", tone: "danger" },
};

function statusMeta(status?: number | string): { label: string; tone: BadgeTone } {
  return STATUS_META[Number(status)] || {
    label: `未知(${status})`,
    tone: "neutral" as BadgeTone,
  };
}

function splitGroups(group?: string): string[] {
  return String(group || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
}

function channelGroupNames(
  channel: Channel,
  allowedGroups: ReadonlySet<string> | null = null,
): string[] {
  const names =
    channel.source_platform === "sub2api"
      ? (channel.groups || []).map((group) => group.name).filter(Boolean)
      : splitGroups(channel.group);
  return allowedGroups ? names.filter((name) => allowedGroups.has(name)) : names;
}

function ratioText(item?: GroupItem): string {
  if (!item) return "—";
  const ratio = item.ratio;
  if (ratio === undefined || ratio === null || ratio === "") return "—";
  return typeof ratio === "number" ? `×${ratio}` : String(ratio);
}

function bindingRatioText(ratio: unknown): string {
  if (ratio === undefined || ratio === null || ratio === "") return "—";
  return typeof ratio === "number" ? `×${ratio}` : `×${String(ratio)}`;
}

function currentKeySummary(binding?: ChannelUpstreamBinding): string {
  const groups = binding?.matched_groups || [];
  if (groups.length) {
    return `✓ 当前 key：${groups
      .map((item) => `${item.name} ${bindingRatioText(item.ratio)}`)
      .join("、")}`;
  }
  if (!binding?.configured) return "待配置对应的本地监控登录态";
  return binding.match_message || "尚未取得当前 key 的上游倍率";
}

function bindingStatusLabel(binding?: ChannelUpstreamBinding): string {
  const status = binding?.match_status || "unmatched";
  if (status === "refresh_error" || status === "error") return "刷新失败";
  if (status === "needs_key_verification") return "需要安全验证";
  if (status === "missing_key") return "缺少渠道 key";
  if (status === "key_not_found") return "key 未匹配";
  if (status === "no_group") return "未配置分组";
  if (status === "inferred") return "分组名匹配";
  if (status === "inferred_partial") return "分组名部分匹配";
  if (status === "inferred_none") return "分组名未命中";
  if (status === "unmatched") return binding?.configured ? "待匹配" : "未配置";
  return "未匹配";
}

function isStaleMatchStatus(status?: string): boolean {
  return (
    status === "refresh_error" ||
    status === "error" ||
    status === "needs_key_verification" ||
    status === "missing_key"
  );
}

function staleMatchPrefix(binding?: ChannelUpstreamBinding): string {
  if (binding?.match_status === "needs_key_verification") {
    return "需要安全验证，显示上次成功倍率";
  }
  if (binding?.match_status === "missing_key") {
    return "缺少渠道 key，显示上次成功倍率";
  }
  return "刷新失败，显示上次成功倍率";
}

function bindingFailure(binding?: ChannelUpstreamBinding): { summary: string; raw: string } {
  const raw = binding?.match_message || "未知错误";
  if (/请填写\s*NewAPI\s*用户名和密码/i.test(raw)) {
    return {
      summary: "当前渠道选择了账号密码认证，但尚未填写用户名和密码；如已配置系统访问令牌，请将认证方式改为令牌",
      raw,
    };
  }
  if (binding?.match_status === "needs_key_verification") {
    return { summary: "需要重新完成渠道 key 安全验证", raw };
  }
  if (binding?.match_status === "missing_key") {
    return { summary: "未取得渠道真实 key", raw };
  }
  return explainUpstreamError(raw);
}

function bindingTooltip(binding?: ChannelUpstreamBinding): string | undefined {
  if (isStaleMatchStatus(binding?.match_status)) return undefined;
  return binding?.match_message || "未匹配";
}

function bindingTone(status?: string): BadgeTone {
  if (status === "matched") return "success";
  if (status === "matched_partial") return "warning";
  if (status === "inferred") return "info";
  if (status === "inferred_partial") return "warning";
  if (status === "inferred_none") return "neutral";
  if (status === "needs_key_verification") return "warning";
  if (status && status !== "unmatched") return "danger";
  return "neutral";
}

// ---- composables ----
const enabled = ref(true);
const { reconcileMode, handleReconcileModeChange } = useReconcileMode(enabled);
const { handleSyncMainSites } = useAppActions();
const toast = useToast();

// ---- state ----
const adminSites = shallowRef<AdminSite[]>([]);
const siteId = ref<number | null>(null);
const adminLoading = ref(true);
const adminFormOpen = ref(false);
const editingAdmin = shallowRef<AdminSite | null>(null);

const channels = shallowRef<Channel[]>([]);
const groups = shallowRef<Record<string, GroupItem>>({});
const groupCatalogReady = ref(false);
const upstreamBindings = shallowRef<Record<string, ChannelUpstreamBinding>>({});
const loading = ref(false);
const error = ref("");
const staleDataWarning = ref("");
const keyword = ref("");
const groupFilter = ref<string | null>(null);
const selectedChannelId = ref<number | null>(null);
const matching = shallowRef<Set<number>>(new Set());
const refreshingKeyIds = shallowRef<Set<number>>(new Set());
const rowNote = shallowRef<Record<number, { ok: boolean; text: string }>>({});
const priorityChannel = shallowRef<Channel | null>(null);
const upstreamConfigChannel = shallowRef<Channel | null>(null);
const sub2ApiChannel = shallowRef<Channel | null>(null);
const updatingChannelIds = shallowRef<Set<number>>(new Set());
const actionError = ref("");
const syncing = ref(false);

// ---- non-reactive refs (React useRef equivalents) ----
let loadVersion = 0;
const automaticallyRefreshedSiteIds = new Set<number>();
let loadedSiteId: number | null = null;
let dataSiteId: number | null = null;

// ---- computed ----
const currentAdminSite = computed(
  () => adminSites.value.find((site) => site.id === siteId.value) || null,
);
const isSub2Api = computed(() => currentAdminSite.value?.platform === "sub2api");

const allowedGroupNames = computed(() =>
  groupCatalogReady.value ? new Set(Object.keys(groups.value)) : null,
);

const groupChannelCount = computed(() => {
  const count: Record<string, number> = {};
  for (const channel of channels.value) {
    for (const group of channelGroupNames(channel, allowedGroupNames.value)) {
      count[group] = (count[group] || 0) + 1;
    }
  }
  return count;
});

const groupRows = computed(() => {
  if (allowedGroupNames.value) return Array.from(allowedGroupNames.value).sort();
  const names = new Set<string>([
    ...Object.keys(groups.value),
    ...Object.keys(groupChannelCount.value),
  ]);
  return Array.from(names).sort();
});

const visibleChannels = computed(() => {
  if (!groupFilter.value) return channels.value;
  return channels.value.filter((channel) =>
    channelGroupNames(channel, allowedGroupNames.value).includes(groupFilter.value!),
  );
});

const sub2ApiGroups = computed<Sub2ApiGroupRef[]>(() =>
  Object.values(groups.value).flatMap((group): Sub2ApiGroupRef[] => {
    if (!group.id) return [];
    return [
      {
        id: group.id,
        name: group.name || `#${group.id}`,
        platform: group.platform,
        status: group.status,
        rate_multiplier: group.rate_multiplier,
      },
    ];
  }),
);

const selectedChannel = computed(
  () => channels.value.find((channel) => channel.id === selectedChannelId.value) || null,
);
const selectedBinding = computed(() =>
  selectedChannel.value
    ? upstreamBindings.value[String(selectedChannel.value.id)]
    : undefined,
);
const selectedBindingStale = computed(() =>
  isStaleMatchStatus(selectedBinding.value?.match_status),
);
const selectedBindingError = computed(() => bindingFailure(selectedBinding.value));

const upstreamConfigBinding = computed(() =>
  upstreamConfigChannel.value
    ? upstreamBindings.value[String(upstreamConfigChannel.value.id)]
    : undefined,
);

/** NewAPI 渠道表格行：预计算所有 binding/note/状态，避免模板内重复调用 */
const newApiRows = computed(() => {
  if (isSub2Api.value) return [];
  return visibleChannels.value.map((channel) => {
    const binding = upstreamBindings.value[String(channel.id)];
    const matchedGroups = binding?.matched_groups || [];
    const staleMatch = isStaleMatchStatus(binding?.match_status);
    const partialMatch = binding?.match_status === "matched_partial";
    const bindingError = bindingFailure(binding);
    const note = rowNote.value[channel.id];
    const displayedGroups = channelGroupNames(channel, allowedGroupNames.value);
    const isMatching = matching.value.has(channel.id);
    const isRefreshingKey = refreshingKeyIds.value.has(channel.id);
    const isSelected = selectedChannelId.value === channel.id;
    return {
      channel,
      meta: statusMeta(channel.status),
      binding,
      matchedGroups,
      staleMatch,
      partialMatch,
      bindingError,
      note,
      displayedGroups,
      isMatching,
      isRefreshingKey,
      isSelected,
    };
  });
});

// ---- functions ----
async function loadAdminSites() {
  adminLoading.value = true;
  try {
    const response = await api.adminSites();
    const list = response.data || [];
    adminSites.value = list;
    error.value = "";
    const previous = siteId.value;
    if (previous && list.some((site) => site.id === previous)) {
      // keep current selection
    } else {
      siteId.value = list[0]?.id ?? null;
    }
  } catch (err) {
    adminSites.value = [];
    error.value = errorText(err, "主站列表加载失败");
  } finally {
    adminLoading.value = false;
  }
}

async function refreshChannelMatches(
  targetSiteId: number,
  channelList: Channel[],
  refreshVersion: number,
): Promise<void> {
  for (const channel of channelList) {
    if (refreshVersion !== loadVersion) return;
    let data: ChannelUpstreamBinding;
    try {
      const response = await api.matchChannelUpstreamBinding(
        targetSiteId,
        channel.id,
      );
      data = response.data || {};
    } catch (err) {
      data = {
        configured: true,
        match_status: "refresh_error",
        match_message: errorText(err, "自动刷新失败"),
        matched_groups: [],
      };
    }
    if (refreshVersion !== loadVersion) return;
    const previous = upstreamBindings.value;
    const previousBinding = previous[String(channel.id)];
    if (
      previousBinding?.matched_groups?.length &&
      !data.matched_groups?.length &&
      isStaleMatchStatus(data.match_status)
    ) {
      upstreamBindings.value = {
        ...previous,
        [String(channel.id)]: {
          ...previousBinding,
          match_status: "refresh_error",
          match_message: data.match_message,
        },
      };
    } else {
      upstreamBindings.value = {
        ...previous,
        [String(channel.id)]: data,
      };
    }
  }
}

async function load(
  query: string,
  options: { refreshMatches?: boolean; waitForMatches?: boolean } = {},
): Promise<boolean> {
  if (siteId.value == null || !currentAdminSite.value) return false;
  const targetSiteId = siteId.value;
  const targetPlatform = currentAdminSite.value.platform;
  const refreshVersion = ++loadVersion;
  loading.value = true;
  error.value = "";
  staleDataWarning.value = "";
  try {
    const [channelResponse, groupResponse, bindingResponse] =
      targetPlatform === "sub2api"
        ? await Promise.all([
            api.channels(targetSiteId, query),
            api.channelGroups(targetSiteId),
            Promise.resolve({ success: true, data: {} as Record<string, ChannelUpstreamBinding> }),
          ])
        : await Promise.all([
            api.channels(targetSiteId, query),
            api
              .channelGroups(targetSiteId)
              .catch(() => ({ success: false as const, data: {} as Record<string, GroupItem> })),
            api
              .channelUpstreamBindings(targetSiteId)
              .catch(() => ({ success: false as const, data: {} as Record<string, ChannelUpstreamBinding> })),
          ]);
    if (refreshVersion !== loadVersion) return false;
    channels.value = channelResponse.data || [];
    groups.value = groupResponse.data || {};
    groupCatalogReady.value = groupResponse.success === true;
    upstreamBindings.value = bindingResponse.data || {};
    dataSiteId = targetSiteId;
    rowNote.value = {};
    actionError.value = "";
    if (targetPlatform === "newapi" && options.refreshMatches) {
      const refreshPromise = refreshChannelMatches(
        targetSiteId,
        channelResponse.data || [],
        refreshVersion,
      );
      if (options.waitForMatches) await refreshPromise;
    }
    return true;
  } catch (err) {
    if (refreshVersion !== loadVersion) return false;
    const message = errorText(err, "主站渠道加载失败");
    if (dataSiteId === targetSiteId) {
      staleDataWarning.value = `刷新失败，当前显示上次成功数据：${message}`;
    } else {
      channels.value = [];
      groups.value = {};
      groupCatalogReady.value = false;
      upstreamBindings.value = {};
      error.value = message;
    }
    return false;
  } finally {
    if (refreshVersion === loadVersion) loading.value = false;
  }
}

async function syncCurrentMainSite() {
  if (siteId.value == null) return;
  syncing.value = true;
  try {
    const synced = await handleSyncMainSites(siteId.value);
    if (!synced) return;
    groupFilter.value = null;
    selectedChannelId.value = null;
    sub2ApiChannel.value = null;
    rowNote.value = {};
    await load(keyword.value);
  } finally {
    syncing.value = false;
  }
}

async function matchUpstream(ch: Channel) {
  if (siteId.value == null) return;
  matching.value = new Set(matching.value).add(ch.id);
  actionError.value = "";
  try {
    const response = await api.matchChannelUpstreamBinding(siteId.value, ch.id);
    if (!response.success && !response.data) {
      throw new Error(response.message || "匹配失败");
    }
    const binding: ChannelUpstreamBinding = response.data || {};
    upstreamBindings.value = {
      ...upstreamBindings.value,
      [String(ch.id)]: binding,
    };
    const staleMatch = isStaleMatchStatus(binding.match_status);
    const explained = staleMatch ? bindingFailure(binding) : null;
    const note = staleMatch
      ? binding.matched_groups?.length
        ? `${staleMatchPrefix(binding)} · 错误原因：${explained!.summary}`
        : `${bindingStatusLabel(binding)}：${explained!.summary}`
      : currentKeySummary(binding);
    rowNote.value = {
      ...rowNote.value,
      [ch.id]: { ok: response.success && !staleMatch, text: note },
    };
    if (staleMatch) {
      if (binding.matched_groups?.length) {
        toast.info(
          `渠道「${ch.name || `#${ch.id}`}」${staleMatchPrefix(binding)}：${explained!.summary}`,
        );
      } else {
        actionError.value =
          `上游匹配失败：${explained!.summary}。原始错误：${explained!.raw}`;
        toast.error(
          `渠道「${ch.name || `#${ch.id}`}」刷新失败：${explained!.summary}`,
        );
      }
    } else if (!response.success) {
      toast.error(
        `渠道「${ch.name || `#${ch.id}`}」匹配失败：${binding.match_message || response.message || "未知错误"}`,
      );
    } else if (!binding.configured) {
      toast.info(`渠道「${ch.name || `#${ch.id}`}」尚未匹配本地监控登录态`);
    } else if (binding.matched_groups?.length) {
      toast.success(`渠道「${ch.name || `#${ch.id}`}」倍率已刷新`);
    } else {
      toast.info(binding.match_message || "未取得当前 key 的上游倍率");
    }
  } catch (err) {
    const message = errorText(err, "匹配失败");
    const explained = explainUpstreamError(message);
    const previousBinding = upstreamBindings.value[String(ch.id)];
    if (previousBinding?.matched_groups?.length) {
      upstreamBindings.value = {
        ...upstreamBindings.value,
        [String(ch.id)]: {
          ...previousBinding,
          match_status: "refresh_error",
          match_message: message,
        },
      };
      rowNote.value = {
        ...rowNote.value,
        [ch.id]: { ok: true, text: "刷新失败，继续显示上次成功倍率" },
      };
      toast.info(
        `渠道「${ch.name || `#${ch.id}`}」刷新失败：${explained.summary}，继续显示上次成功倍率`,
      );
    } else {
      rowNote.value = {
        ...rowNote.value,
        [ch.id]: { ok: false, text: `刷新失败：${explained.summary}` },
      };
      actionError.value =
        `上游匹配失败：${explained.summary}。原始错误：${explained.raw}`;
      toast.error(
        `渠道「${ch.name || `#${ch.id}`}」刷新失败：${explained.summary}`,
      );
    }
  } finally {
    const next = new Set(matching.value);
    next.delete(ch.id);
    matching.value = next;
  }
}

async function refreshChannelKey(ch: Channel) {
  if (siteId.value == null || refreshingKeyIds.value.size > 0) return;
  refreshingKeyIds.value = new Set([ch.id]);
  actionError.value = "";
  try {
    const response = await api.refreshChannelKey(siteId.value, ch.id);
    const data = response.data;
    if (!response.success || !data) {
      throw new Error(response.message || "渠道 key 刷新失败");
    }
    if (data.binding) {
      upstreamBindings.value = {
        ...upstreamBindings.value,
        [String(ch.id)]: data.binding,
      };
    }
    const keyMessage = data.first_fetch
      ? "真实 key 已保存"
      : data.changed
        ? "key 已更新"
        : "key 已是最新";
    const text = data.match_success
      ? `${keyMessage}，倍率已刷新`
      : `${keyMessage}，但倍率刷新失败：${data.match_message || "未知错误"}`;
    rowNote.value = {
      ...rowNote.value,
      [ch.id]: { ok: data.match_success, text },
    };
    if (data.match_success) toast.success(`渠道「${ch.name || `#${ch.id}`}」${text}`);
    else toast.info(`渠道「${ch.name || `#${ch.id}`}」${text}`);
  } catch (err) {
    const message = errorText(err, "渠道 key 刷新失败");
    const explained = explainUpstreamError(message);
    rowNote.value = {
      ...rowNote.value,
      [ch.id]: { ok: false, text: `刷新 key 失败：${explained.summary}` },
    };
    actionError.value =
      `渠道「${ch.name || `#${ch.id}`}」刷新 key 失败：${explained.summary}。原始错误：${explained.raw}`;
    toast.error(`渠道「${ch.name || `#${ch.id}`}」刷新 key 失败：${explained.summary}`);
  } finally {
    refreshingKeyIds.value = new Set();
  }
}

async function submitForm(channel: Channel, priority: number) {
  actionError.value = "";
  try {
    const response = await api.updateChannel(siteId.value!, channel.id, { priority });
    if (!response.success) {
      throw new Error(response.message || "优先级保存失败");
    }
    await load(keyword.value);
    toast.success(`渠道「${channel.name || `#${channel.id}`}」优先级已更新`);
  } catch (err) {
    const message = errorText(err, "优先级保存失败");
    actionError.value = `优先级保存失败：${message}`;
    toast.error(`优先级保存失败：${message}`);
    throw new Error(message);
  }
}

function setChannelUpdating(channelId: number, updating: boolean) {
  const next = new Set(updatingChannelIds.value);
  if (updating) next.add(channelId);
  else next.delete(channelId);
  updatingChannelIds.value = next;
}

async function refreshSub2ApiChannel(channel: Channel) {
  setChannelUpdating(channel.id, true);
  const refreshed = await load(keyword.value);
  setChannelUpdating(channel.id, false);
  if (refreshed) {
    toast.success(`渠道「${channel.name || `#${channel.id}`}」配置已刷新`);
  } else {
    toast.error(`渠道「${channel.name || `#${channel.id}`}」刷新失败`);
  }
}

async function toggleSub2ApiChannel(channel: Channel) {
  if (siteId.value == null || !currentAdminSite.value?.capabilities?.toggle_channel) return;
  const nextStatus =
    normalizedChannelStatus(channel) === "active" ? "disabled" : "active";
  const label = channel.name || `#${channel.id}`;
  setChannelUpdating(channel.id, true);
  actionError.value = "";
  try {
    const response = await api.updateChannel(siteId.value, channel.id, {
      status: nextStatus,
    });
    if (!response.success) throw new Error(response.message || "状态更新失败");
    await load(keyword.value);
    toast.success(`渠道「${label}」已${nextStatus === "active" ? "启用" : "停用"}`);
  } catch (err) {
    const message = errorText(err, "状态更新失败");
    actionError.value = `渠道「${label}」状态更新失败：${message}`;
    toast.error(`渠道「${label}」状态更新失败：${message}`);
  } finally {
    setChannelUpdating(channel.id, false);
  }
}

async function submitSub2ApiChannel(patch: Partial<Channel>): Promise<void> {
  if (siteId.value == null || !sub2ApiChannel.value) return;
  const channel = sub2ApiChannel.value;
  const label = channel.name || `#${channel.id}`;
  setChannelUpdating(channel.id, true);
  actionError.value = "";
  try {
    const response = await api.updateChannel(siteId.value, channel.id, patch);
    if (!response.success) throw new Error(response.message || "渠道配置保存失败");
    await load(keyword.value);
    toast.success(`渠道「${label}」配置已保存`);
  } catch (err) {
    const message = errorText(err, "渠道配置保存失败");
    actionError.value = `渠道「${label}」保存失败：${message}`;
    toast.error(`渠道「${label}」保存失败：${message}`);
    throw new Error(message);
  } finally {
    setChannelUpdating(channel.id, false);
  }
}

async function onUpstreamConfigSubmit(
  _payload: Partial<Channel>,
  bindingPayload: ChannelUpstreamBindingPayload,
): Promise<void> {
  if (!upstreamConfigChannel.value || siteId.value == null) return;
  const channel = upstreamConfigChannel.value;
  const result = await api.saveChannelUpstreamBinding(
    siteId.value,
    channel.id,
    bindingPayload,
  );
  if (!result.success) {
    throw new Error(result.message || "保存上游配置失败");
  }
  upstreamBindings.value = {
    ...upstreamBindings.value,
    [String(channel.id)]:
      result.data || {
        configured: !!bindingPayload.upstream_base_url,
        upstream_base_url: bindingPayload.upstream_base_url,
        upstream_platform: bindingPayload.upstream_platform,
        auth_mode: bindingPayload.auth_mode,
        match_status: "unmatched",
        matched_groups: [],
      },
  };
  toast.success(
    `渠道「${channel.name || `#${channel.id}`}」上游配置已保存`,
  );
}

async function onPrioritySubmit(priority: number): Promise<void> {
  if (!priorityChannel.value) return;
  return submitForm(priorityChannel.value, priority);
}

async function onAdminVerified() {
  await load("");
}

function onSiteIdChange(v: string) {
  siteId.value = Number(v);
}

function onReconcileModeChange(v: string) {
  handleReconcileModeChange(v as ReconcileMode);
}

function search() {
  void load(keyword.value);
}

function toggleSelectChannel(channel: Channel) {
  const isSelected = selectedChannelId.value === channel.id;
  selectedChannelId.value = isSelected ? null : channel.id;
}

function editCurrentAdmin() {
  editingAdmin.value = currentAdminSite.value;
  adminFormOpen.value = true;
}

function addAdmin() {
  editingAdmin.value = null;
  adminFormOpen.value = true;
}

// ---- effects ----
onMounted(() => {
  void loadAdminSites();
});

watch(
  [siteId, () => currentAdminSite.value?.platform],
  () => {
    groupFilter.value = null;
    selectedChannelId.value = null;
    sub2ApiChannel.value = null;
    rowNote.value = {};
    matching.value = new Set();
    updatingChannelIds.value = new Set();
    staleDataWarning.value = "";
    if (siteId.value == null) {
      loadedSiteId = null;
      dataSiteId = null;
      channels.value = [];
      groups.value = {};
      upstreamBindings.value = {};
      return;
    }
    if (loadedSiteId === siteId.value) return;
    loadedSiteId = siteId.value;
    dataSiteId = null;
    channels.value = [];
    groups.value = {};
    groupCatalogReady.value = false;
    upstreamBindings.value = {};
    const refreshMatches =
      currentAdminSite.value?.platform === "newapi" &&
      claimAutomaticRefresh(automaticallyRefreshedSiteIds, siteId.value);
    void load("", { refreshMatches });
  },
);
</script>

<template>
  <!-- 无主站空态 -->
  <template v-if="!adminLoading && !adminSites.length">
    <Panel title="主站监控" subtitle="需要先添加 NewAPI 或 sub2api 主站连接">
      <div v-if="error" class="mb-3 rounded-[var(--radius-sm)] bg-danger-bg px-3 py-2 text-sm text-danger-fg">
        {{ error }}
      </div>
      <div class="flex flex-col gap-4 py-8 text-center">
        <p class="text-sm text-ink-muted">
          添加你的 NewAPI 或 sub2api 主站后，这里实时读取已有渠道。
          <br />
          NewAPI 可调整优先级；sub2api 可编辑渠道配置并启停渠道。
        </p>
        <Button @click="addAdmin">
          添加主站
        </Button>
      </div>
    </Panel>
    <AdminSiteFormDialog
      :open="adminFormOpen"
      :site="editingAdmin"
      @close="adminFormOpen = false"
      :on-saved="loadAdminSites"
    />
  </template>

  <!-- 主视图 -->
  <div v-else class="flex flex-col gap-4">
    <PageHeader
      title="主站监控"
      subtitle="统一读取 NewAPI / sub2api 主站渠道，并按平台能力提供安全的管理操作。"
    />

    <div
      v-if="actionError"
      class="sticky top-[68px] z-30 flex items-start justify-between gap-3 rounded-[var(--radius-sm)] border border-[var(--color-danger-fg)]/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg shadow-[var(--shadow-floating)]"
    >
      <span>{{ actionError }}</span>
      <button
        class="shrink-0 text-[12.5px] opacity-70 hover:opacity-100"
        @click="actionError = ''"
      >
        关闭
      </button>
    </div>

    <Panel
      title="主站渠道"
      :subtitle="
        isSub2Api
          ? '实时读取 sub2api 渠道与分组配置；支持完整编辑和启停，不读取号池'
          : '实时读取 NewAPI /api/channel；本页面仅允许调整优先级'
      "
    >
      <template #action>
        <div class="flex flex-wrap items-center gap-2">
          <Select
            class="w-full sm:w-auto sm:min-w-[200px]"
            :model-value="siteId ?? ''"
            @update:model-value="onSiteIdChange"
          >
            <option v-for="site in adminSites" :key="site.id" :value="site.id">
              {{ site.platform_label || (site.platform === "sub2api" ? "sub2api" : "NewAPI") }} · {{ site.name }} · {{ site.base_url
              }}<span v-if="site.platform === 'sub2api' && !site.has_login_password"> · 未配置管理员密码</span><span v-else-if="!site.has_access_token"> · 未配置令牌</span>
            </option>
          </Select>
          <label class="flex shrink-0 items-center gap-1.5 text-[12.5px] font-medium text-ink-muted">
            <span>消失渠道</span>
            <Select
              class="w-[4.5rem]"
              :model-value="reconcileMode"
              @update:model-value="onReconcileModeChange"
              aria-label="上游消失渠道的处理方式"
              title="主站同步时，上游已消失的监控渠道如何处理"
            >
              <option value="disable">停用</option>
              <option value="delete">删除</option>
            </Select>
          </label>
          <Button
            variant="secondary"
            aria-label="同步主站渠道"
            title="同步当前主站的全部渠道和分组，并对账消失渠道"
            :disabled="siteId == null"
            :loading="syncing"
            @click="syncCurrentMainSite"
          >
            <Cloud v-if="!syncing" :size="13" />
            同步主站
          </Button>
          <Button
            variant="secondary"
            :disabled="!currentAdminSite"
            @click="editCurrentAdmin"
          >
            编辑主站
          </Button>
          <Button
            variant="secondary"
            @click="addAdmin"
          >
            添加主站
          </Button>
          <span class="mx-1 h-5 w-px bg-line-strong" aria-hidden="true" />
          <Input
            class="w-full sm:w-auto sm:min-w-[170px]"
            :placeholder="isSub2Api ? '搜索渠道名/模型' : '搜索渠道名/密钥/模型'"
            :model-value="keyword"
            @update:model-value="(v: string) => (keyword = v)"
            @keydown.enter="search"
          />
          <Button variant="secondary" @click="search">
            搜索
          </Button>
        </div>
      </template>

      <div v-if="error" class="mb-3 rounded-[var(--radius-sm)] bg-danger-bg px-3 py-2 text-sm text-danger-fg">
        {{ error }}
      </div>

      <div v-if="staleDataWarning" class="mb-3 rounded-[var(--radius-sm)] border border-[var(--color-warning-fg)]/25 bg-warning-bg px-3 py-2 text-sm text-warning-fg">
        {{ staleDataWarning }}，数据可能已过期。
      </div>

      <div class="grid grid-cols-1 gap-4 lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[280px_minmax(0,1fr)]">
        <!-- 分组视角侧边栏 -->
        <aside class="space-y-2">
          <div class="flex items-center justify-between px-1">
            <span class="text-[12.5px] font-bold text-ink-muted">
              分组视角
            </span>
            <button
              v-if="groupFilter"
              class="text-[11px] text-accent"
              @click="groupFilter = null"
            >
              清除
            </button>
          </div>
          <button
            :class="[
              'w-full rounded-[var(--radius-sm)] border px-3 py-2 text-left text-sm transition',
              groupFilter === null
                ? 'border-[var(--color-accent)] bg-sunken'
                : 'border-line hover:bg-sunken-hover',
            ]"
            @click="groupFilter = null"
          >
            <span class="font-semibold text-ink-strong">
              全部渠道
            </span>
            <span class="float-right tabular-nums text-ink-muted">
              {{ channels.length }}
            </span>
          </button>
          <button
            v-for="name in groupRows"
            :key="name"
            :class="[
              'w-full rounded-[var(--radius-sm)] border px-3 py-2 text-left transition',
              groupFilter === name
                ? 'border-[var(--color-accent)] bg-sunken'
                : 'border-line hover:bg-sunken-hover',
            ]"
            @click="groupFilter = groupFilter === name ? null : name"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="truncate font-semibold text-ink-strong">
                {{ name }}
              </span>
              <span class="tabular-nums text-[12.5px] text-ink-muted">
                {{ groupChannelCount[name] || 0 }} 渠道
              </span>
            </div>
            <div class="mt-1 flex items-center gap-2">
              <Badge :tone="groups[name] ? 'info' : 'neutral'">
                倍率 {{ ratioText(groups[name]) }}
              </Badge>
              <Badge :tone="groups[name] ? 'success' : 'warning'" dot>
                {{ groups[name] ? "上游已配置" : "仅渠道引用" }}
              </Badge>
            </div>
            <div v-if="groups[name]?.desc" class="mt-1 truncate text-[11px] text-ink-soft">
              {{ groups[name].desc }}
            </div>
          </button>
        </aside>

        <!-- 渠道主区域 -->
        <div class="min-w-0">
          <div v-if="loading" class="py-16 text-center text-sm text-ink-muted">
            加载中...
          </div>
          <div v-else-if="!visibleChannels.length" class="py-16 text-center text-sm text-ink-muted">
            {{ groupFilter ? `分组「${groupFilter}」下暂无渠道` : "主站当前没有可显示的渠道" }}
          </div>
          <Sub2ApiChannelTable
            v-else-if="isSub2Api"
            :channels="visibleChannels"
            :busy-channel-ids="updatingChannelIds"
            :can-edit="Boolean(currentAdminSite?.capabilities?.edit_channel)"
            :can-toggle="Boolean(currentAdminSite?.capabilities?.toggle_channel)"
            @edit="(ch: Channel) => (sub2ApiChannel = ch)"
            @toggle="toggleSub2ApiChannel"
            @refresh="refreshSub2ApiChannel"
          />
          <div v-else class="priceai-scrollbar max-h-[calc(100vh-18rem)] overflow-auto rounded-[var(--radius-sm)]">
            <table class="w-full min-w-[1040px] table-fixed text-left text-sm">
              <colgroup>
                <col class="w-[170px]" />
                <col class="w-[112px]" />
                <col class="w-[190px]" />
                <col class="w-[72px]" />
                <col class="w-[72px]" />
                <col class="w-[92px]" />
                <col class="w-[270px]" />
              </colgroup>
              <thead class="sticky top-0 z-10 bg-panel">
                <tr class="border-b border-line-soft text-[12.5px] font-semibold text-ink-muted">
                  <th class="pb-2">渠道</th>
                  <th class="pb-2">分组</th>
                  <th class="pb-2">当前 key 上游倍率</th>
                  <th class="pb-2">权重</th>
                  <th class="pb-2">优先级</th>
                  <th class="pb-2">状态</th>
                  <th class="pb-2">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in newApiRows"
                  :key="row.channel.id"
                  :class="[
                    'border-b border-line-soft last:border-0 hover:bg-sunken-hover',
                    row.isSelected ? 'bg-sunken' : '',
                  ]"
                >
                  <td class="max-w-0 py-3 pr-3">
                    <button
                      class="max-w-full text-left"
                      @click="toggleSelectChannel(row.channel)"
                    >
                      <div class="truncate font-bold text-ink-strong">
                        {{ row.channel.name || `#${row.channel.id}` }}
                      </div>
                      <div class="truncate text-[11px] text-ink-soft">
                        #{{ row.channel.id }} · 类型 {{ row.channel.type ?? "—" }}{{ row.channel.models ? ` · ${splitGroups(row.channel.models).length} 模型` : "" }}
                      </div>
                    </button>
                    <div v-if="row.isMatching" class="mt-1 flex items-center gap-1.5 text-[11px] font-semibold text-accent">
                      <Spinner />
                      匹配中...
                    </div>
                    <div
                      v-else-if="row.note"
                      :class="['mt-1 truncate text-[11px]', row.note.ok ? 'text-ink-muted' : 'text-danger-fg']"
                      :title="row.note.text"
                    >
                      {{ row.note.text }}
                    </div>
                  </td>
                  <td class="max-w-0 py-3 pr-3">
                    <div class="flex flex-wrap gap-1">
                      <Badge
                        v-for="group in row.displayedGroups"
                        :key="group"
                        :tone="groups[group] ? 'info' : 'neutral'"
                      >
                        {{ group }}
                      </Badge>
                      <span v-if="!row.displayedGroups.length" class="text-[12.5px] text-ink-soft">—</span>
                    </div>
                  </td>
                  <td class="max-w-0 py-3 pr-3">
                    <div v-if="row.matchedGroups.length" class="flex max-w-full flex-wrap gap-1 overflow-hidden">
                      <Badge
                        v-for="item in row.matchedGroups"
                        :key="item.name"
                        :tone="item.available_to_login === false ? 'warning' : 'success'"
                        class="max-w-full truncate"
                      >
                        {{ item.name }} {{ bindingRatioText(item.ratio) }}
                      </Badge>
                    </div>
                    <Badge
                      v-else
                      :tone="bindingTone(row.binding?.match_status)"
                      class="max-w-full truncate"
                      :title="bindingTooltip(row.binding)"
                    >
                      {{ bindingStatusLabel(row.binding) }}
                    </Badge>
                    <div
                      v-if="row.staleMatch"
                      class="mt-1 truncate text-[10px] text-warning-fg"
                      :title="row.bindingError.raw"
                    >
                      <template v-if="row.matchedGroups.length">{{ staleMatchPrefix(row.binding) }} · </template>
                      错误原因：{{ row.bindingError.summary }}
                    </div>
                    <div
                      v-else-if="row.partialMatch"
                      class="mt-1 truncate text-[10px] text-warning-fg"
                      :title="row.binding?.match_message || '部分分组数据不完整'"
                    >
                      部分匹配：{{ row.binding?.match_message || "部分分组数据不完整" }}
                    </div>
                    <div
                      v-else-if="row.binding?.matched_groups?.length"
                      class="mt-1 truncate text-[10px] text-ink-soft"
                      :title="currentKeySummary(row.binding)"
                    >
                      {{ currentKeySummary(row.binding) }}
                    </div>
                  </td>
                  <td class="py-3 pr-3 tabular-nums text-ink">
                    {{ Number(row.channel.weight ?? 0) }}
                  </td>
                  <td class="py-3 pr-3 tabular-nums font-semibold text-ink-strong">
                    {{ Number(row.channel.priority ?? 0) }}
                  </td>
                  <td class="py-3 pr-3">
                    <Badge :tone="row.meta.tone" dot>
                      {{ row.meta.label }}
                    </Badge>
                  </td>
                  <td class="py-3">
                    <div class="flex flex-nowrap items-center gap-1.5">
                      <Button
                        variant="secondary"
                        size="sm"
                        class="whitespace-nowrap"
                        :loading="row.isMatching"
                        @click="matchUpstream(row.channel)"
                      >
                        <RefreshCw v-if="!row.isMatching" :size="13" />
                        刷新倍率
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        class="whitespace-nowrap"
                        :loading="row.isRefreshingKey"
                        :disabled="refreshingKeyIds.size > 0 && !row.isRefreshingKey"
                        title="强制读取当前渠道真实 key，并重新匹配倍率"
                        @click="refreshChannelKey(row.channel)"
                      >
                        <KeyRound v-if="!row.isRefreshingKey" :size="13" />
                        刷新 key
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        class="whitespace-nowrap"
                        @click="priorityChannel = row.channel"
                      >
                        编辑优先级
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        class="whitespace-nowrap"
                        title="配置该渠道的上游认证，用于 key 精确匹配分组倍率"
                        @click="upstreamConfigChannel = row.channel"
                      >
                        <Settings2 :size="13" />
                        配置上游
                      </Button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Panel>

    <!-- 倍率详情（仅 NewAPI 且选中渠道时展示） -->
    <Panel
      v-if="!isSub2Api && selectedChannel"
      :title="`倍率详情 · ${selectedChannel.name || `#${selectedChannel.id}`}`"
      :subtitle="
        selectedBindingStale
          ? '以下为上次成功匹配的分组与倍率'
          : '当前渠道 key 匹配到的上游分组与倍率'
      "
    >
      <template #action>
        <button
          class="text-[12.5px] text-ink-muted"
          @click="selectedChannelId = null"
        >
          收起
        </button>
      </template>

      <div v-if="selectedBindingStale" class="mb-3 rounded-[var(--radius-sm)] bg-warning-bg px-3 py-2 text-sm text-warning-fg" :title="selectedBindingError.raw">
        {{ staleMatchPrefix(selectedBinding) }} · 错误原因：{{ selectedBindingError.summary }}
      </div>

      <div class="priceai-scrollbar overflow-x-auto pb-1">
        <table class="w-full min-w-max table-auto text-left text-sm">
          <thead>
            <tr class="border-b border-line-soft text-[12.5px] font-semibold text-ink-muted">
              <th class="pb-2">分组</th>
              <th class="pb-2">上游倍率</th>
              <th class="pb-2">分组状态</th>
              <th class="pb-2">描述</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in (selectedBinding?.matched_groups || [])"
              :key="item.name"
              class="border-b border-line-soft last:border-0"
            >
              <td class="py-2 pr-3 font-semibold text-ink-strong">
                {{ item.name }}
              </td>
              <td class="py-2 pr-3 tabular-nums">
                {{ bindingRatioText(item.ratio) }}
              </td>
              <td class="py-2 pr-3">
                <Badge
                  :tone="
                    selectedBindingStale || item.available_to_login === false
                      ? 'warning'
                      : 'success'
                  "
                  dot
                >
                  {{ selectedBindingStale
                    ? "上次成功数据"
                    : item.available_to_login === false
                      ? "上游未找到该分组"
                      : "上游已配置"
                  }}
                </Badge>
              </td>
              <td class="py-2 pr-3 text-ink-muted">
                {{ item.desc || "—" }}
              </td>
            </tr>
            <tr v-if="!(selectedBinding?.matched_groups || []).length">
              <td colspan="4" class="py-3 text-center text-ink-soft">
                {{ isStaleMatchStatus(selectedBinding?.match_status)
                  ? `错误原因：${bindingFailure(selectedBinding).summary}`
                  : selectedBinding?.match_message || "尚未取得上游分组倍率"
                }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </Panel>

    <!-- 弹窗 -->
    <ChannelPriorityDialog
      :open="priorityChannel !== null"
      :channel="priorityChannel"
      @close="priorityChannel = null"
      :on-submit="onPrioritySubmit"
    />

    <Sub2ApiChannelDialog
      :open="sub2ApiChannel !== null"
      :channel="sub2ApiChannel"
      :groups="sub2ApiGroups"
      @close="sub2ApiChannel = null"
      :on-submit="submitSub2ApiChannel"
    />

    <ChannelFormDialog
      v-if="upstreamConfigChannel && siteId != null"
      open
      :channel="upstreamConfigChannel"
      :binding="upstreamConfigBinding"
      :group-names="Object.keys(groups).sort()"
      @close="upstreamConfigChannel = null"
      :on-submit="onUpstreamConfigSubmit"
    />

    <AdminSiteFormDialog
      :open="adminFormOpen"
      :site="editingAdmin"
      @close="adminFormOpen = false"
      :on-saved="loadAdminSites"
      :on-verified="onAdminVerified"
    />
  </div>
</template>
