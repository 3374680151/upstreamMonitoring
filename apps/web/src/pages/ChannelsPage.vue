<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from "vue";
import { Cloud, KeyRound, Percent, RefreshCw } from "lucide-vue-next";
import AdminSiteFormDialog from "@/components/AdminSiteFormDialog.vue";
import AdminTwoFaDialog from "@/components/AdminTwoFaDialog.vue";
import Badge from "@/components/Badge.vue";
import ChannelPriorityDialog from "@/components/ChannelPriorityDialog.vue";
import PageHeader from "@/components/PageHeader.vue";
import Panel from "@/components/Panel.vue";
import Sub2ApiChannelDialog from "@/components/Sub2ApiChannelDialog.vue";
import Sub2ApiChannelTable from "@/components/Sub2ApiChannelTable.vue";
import SyncScopeDialog from "@/components/SyncScopeDialog.vue";
import UpstreamGroupsPopover from "@/components/UpstreamGroupsPopover.vue";
import { Button, EmptyState, Select, Spinner } from "@/components/ui";
import { claimAutomaticRefresh } from "@/lib/automaticRefresh";
import { api } from "@/lib/api";
import { ratioXText } from "@/lib/format";
import { normalizedChannelStatus } from "@/lib/sub2apiChannel";
import { explainUpstreamError } from "@/lib/upstreamError";
import { errorText, useToast } from "@/composables/useToast";
import { useAppActions } from "@/composables/useAppActions";
import { useConsoleData } from "@/composables/useConsoleData";
import { useReconcileMode, type ReconcileMode } from "@/composables/useReconcileMode";
import type {
  AdminSite,
  Channel,
  ChannelDiscoveryCandidate,
  ChannelUpstreamBinding,
  GroupItem,
  Site,
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

function bindingStatusLabel(binding?: ChannelUpstreamBinding): string {
  const status = binding?.match_status || "unmatched";
  if (status === "refresh_error" || status === "error") return "刷新失败";
  if (status === "needs_key_verification") return "需要安全验证";
  if (status === "missing_key") return "缺少渠道 key";
  if (status === "key_not_found") return "key 已不在上游";
  if (status === "no_group") return "key 未绑定分组";
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

/** 认证被拒（登录态失效/未登录）类错误：主徽章直接显示「站点未登录」 */
const AUTH_EXPIRED_MESSAGE_PATTERN =
  /(未登录|登录态已失效|登录态失效|登录已过期|令牌已过期|令牌无效|认证失败|HTTP\s*401|unauthorized|token\s+has\s+(expired|been\s+revoked))/i;
const WAF_MESSAGE_PATTERN = /(WAF|防护拦截)/i;

function isAuthExpiredBinding(binding?: ChannelUpstreamBinding): boolean {
  const status = binding?.match_status;
  if (status !== "error" && status !== "refresh_error") return false;
  const message = String(binding?.match_message || "");
  return !WAF_MESSAGE_PATTERN.test(message) && AUTH_EXPIRED_MESSAGE_PATTERN.test(message);
}

function staleMatchPrefix(binding?: ChannelUpstreamBinding): string {
  if (binding?.match_status === "needs_key_verification") {
    return "需要安全验证，显示上次成功倍率";
  }
  if (binding?.match_status === "missing_key") {
    return "缺少渠道 key，显示上次成功倍率";
  }
  if (isAuthExpiredBinding(binding)) {
    return "站点未登录，显示上次成功倍率";
  }
  if (binding?.match_status === "key_not_found") {
    return "上游分组已失效，显示上次成功倍率";
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

// ---- composables ----
const enabled = ref(true);
const { reconcileMode, handleReconcileModeChange } = useReconcileMode(enabled);
const { handleSyncMainSites } = useAppActions();
const toast = useToast();
// 只读共享监控站点数据（App.vue 激活 + 15s 轮询），用于 hover 浮层展示上游分组目录
const { sites: monitorSites } = useConsoleData();

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
const groupFilter = ref<string | null>(null);
const selectedChannelId = ref<number | null>(null);
const matching = shallowRef<Set<number>>(new Set());
const refreshingKeyIds = shallowRef<Set<number>>(new Set());
const rowNote = shallowRef<Record<number, { ok: boolean; text: string }>>({});
const priorityChannel = shallowRef<Channel | null>(null);
const sub2ApiChannel = shallowRef<Channel | null>(null);
const updatingChannelIds = shallowRef<Set<number>>(new Set());
const actionError = ref("");
const syncing = ref(false);

// ---- 非响应式变量（跨渲染保持，不触发视图更新）----
let loadVersion = 0;
const automaticallyRefreshedSiteIds = new Set<number>();
let loadedSiteId: number | null = null;
let dataSiteId: number | null = null;

// ---- computed ----
const currentAdminSite = computed(
  () => adminSites.value.find((site) => site.id === siteId.value) || null,
);
const isSub2Api = computed(() => currentAdminSite.value?.platform === "sub2api");
const currentKeyRefresh = computed(() => currentAdminSite.value?.key_refresh || null);
const currentRatioRefresh = computed(() => currentAdminSite.value?.ratio_refresh || null);
const ratioRefreshTriggering = ref(false);

// ---- 主站 2FA 就地验证弹窗（proof 缺失或批次暂停时，刷新动作先补验证再续跑）----
const twoFaDialogOpen = ref(false);
const twoFaIntent = ref<"key" | "ratio">("key");
/** 验证通过后要重放的待办动作；非响应式，与触发时的上下文绑定 */
let pendingTwoFaAction: (() => Promise<void>) | null = null;

const keyRefreshStatusText = computed(() => {
  const progress = currentKeyRefresh.value;
  if (!progress) return "";
  const failedSuffix = progress.failed ? ` · 失败 ${progress.failed}` : "";
  if (progress.status === "running") {
    return `key 刷新中 ${progress.done}/${progress.total}${failedSuffix}`;
  }
  if (progress.status === "paused") return "key 刷新已暂停，需重新验证 2FA";
  if (progress.status === "failed") {
    return `key 刷新失败${progress.message ? `：${progress.message}` : ""}`;
  }
  return `key 刷新完成 ${progress.done}/${progress.total}${failedSuffix}`;
});

const ratioRefreshStatusText = computed(() => {
  const progress = currentRatioRefresh.value;
  if (!progress) return "";
  const failedSuffix = progress.failed ? ` · 失败 ${progress.failed}` : "";
  if (progress.status === "running") {
    return `倍率刷新中 ${progress.done}/${progress.total}${failedSuffix}`;
  }
  if (progress.status === "paused") return "倍率刷新已暂停，需重新验证 2FA";
  if (progress.status === "failed") {
    return `倍率刷新失败${progress.message ? `：${progress.message}` : ""}`;
  }
  return `倍率刷新完成 ${progress.done}/${progress.total}${failedSuffix}`;
});

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

// 分组视角的自定义排序：拖拽侧栏卡片调整顺序，localStorage 持久化；
// 顺序只约束当前仍存在的分组，新增分组按默认字母序追加，不丢项。
const GROUP_ORDER_STORAGE_KEY = "upstream.group_view_order";

function loadGroupOrder(): string[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(GROUP_ORDER_STORAGE_KEY) || "[]");
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string")
      : [];
  } catch {
    return [];
  }
}

// 拖拽期间原卡片保留在原位（半透明），占位卡跟随指针指示落点。
// 注意不能把拖拽源从列表移除——源元素离开 DOM 会直接取消原生拖拽。
const customGroupOrder = ref<string[]>(loadGroupOrder());
const dragGroupName = ref<string | null>(null);
const dragInsertIndex = ref<number | null>(null);
const dragPlaceholderHeight = ref(96);

watch(customGroupOrder, (order) => {
  try {
    localStorage.setItem(GROUP_ORDER_STORAGE_KEY, JSON.stringify(order));
  } catch {
    // localStorage 不可用（隐私模式等）时静默降级，顺序仅本次会话生效
  }
});

const groupRows = computed(() => {
  const names = allowedGroupNames.value
    ? new Set<string>(allowedGroupNames.value)
    : new Set<string>([
        ...Object.keys(groups.value),
        ...Object.keys(groupChannelCount.value),
      ]);
  const defaults = Array.from(names).sort();
  const ordered = customGroupOrder.value.filter((name) => names.has(name));
  return [...ordered, ...defaults.filter((name) => !ordered.includes(name))];
});

function clearGroupDragState() {
  dragGroupName.value = null;
  dragInsertIndex.value = null;
}

function onGroupDragStart(name: string, event: DragEvent) {
  dragGroupName.value = name;
  dragPlaceholderHeight.value =
    event.currentTarget instanceof HTMLElement
      ? event.currentTarget.offsetHeight
      : 96;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", name);
  }
}

function onGroupListDragOver(event: DragEvent) {
  if (!dragGroupName.value) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
  const container = event.currentTarget as HTMLElement;
  const cards = Array.from(
    container.querySelectorAll<HTMLElement>("[data-group-card]"),
  );
  let index = cards.length;
  for (let i = 0; i < cards.length; i += 1) {
    const rect = cards[i].getBoundingClientRect();
    if (event.clientY < rect.top + rect.height / 2) {
      index = i;
      break;
    }
  }
  dragInsertIndex.value = index;
}

function onGroupListDragLeave(event: DragEvent) {
  const container = event.currentTarget as HTMLElement;
  if (!container.contains(event.relatedTarget as Node | null)) {
    dragInsertIndex.value = null;
  }
}

function onGroupListDrop() {
  const dragged = dragGroupName.value;
  const index = dragInsertIndex.value;
  clearGroupDragState();
  if (!dragged || index == null) return;
  const current = [...groupRows.value];
  const from = current.indexOf(dragged);
  if (from < 0) return;
  current.splice(from, 1);
  current.splice(index > from ? index - 1 : index, 0, dragged);
  customGroupOrder.value = current;
}

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

/** Base URL 归一化：去协议、去尾斜杠、小写，用于匹配 binding 上游与监控站点 */
function normalizeBaseUrlKey(url: unknown): string {
  return String(url || "")
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .replace(/\/+$/, "");
}

/** 监控站点 base_url → 站点 索引，供渠道行解析自己的上游目录 */
const monitorSiteByBaseUrl = computed(() => {
  const map = new Map<string, Site>();
  for (const site of monitorSites.value) {
    const key = normalizeBaseUrlKey(site.base_url);
    if (key) map.set(key, site);
  }
  return map;
});

/** 按渠道上游地址找同源监控站点。
 *  继承监控站登录态时 binding 不落 upstream_base_url，与后端
 *  find_monitor_site_for_channel 一致：以渠道自身 base_url 为准。 */
function upstreamSiteFor(
  channel: Channel,
  binding?: ChannelUpstreamBinding,
): Site | null {
  const key =
    normalizeBaseUrlKey(binding?.upstream_base_url) ||
    normalizeBaseUrlKey(channel.base_url);
  if (!key) return null;
  const site = monitorSiteByBaseUrl.value.get(key);
  if (!site) return null;
  return site;
}

/** NewAPI 渠道表格行：预计算所有 binding/note/状态，避免模板内重复调用 */
const newApiRows = computed(() => {
  if (isSub2Api.value) return [];
  return visibleChannels.value.map((channel) => {
    const binding = upstreamBindings.value[String(channel.id)];
    const matchedGroups = binding?.matched_groups || [];
    const note = rowNote.value[channel.id];
    const displayedGroups = channelGroupNames(channel, allowedGroupNames.value);
    const isMatching = matching.value.has(channel.id);
    const isRefreshingKey = refreshingKeyIds.value.has(channel.id);
    const isSelected = selectedChannelId.value === channel.id;
    const upstreamSite = upstreamSiteFor(channel, binding);
    // 展示规则：出问题时先看上游站点登录态——站点没登录一律归为「站点未登录」，
    // 站点有登录但 key 有问题才显示「上游 key 未匹配」等具体原因。
    const siteLoggedOut = !!upstreamSite && upstreamSite.auth_ready === false;
    // 站点登录态失效（auth_ready=false 或匹配时被 401 拒绝）但上次成功匹配过
    // 倍率：照常展示该份数据，用中性色 + 悬浮提示特殊标注为旧数据。
    const siteUnauthenticated = siteLoggedOut || isAuthExpiredBinding(binding);
    const hasRatio = matchedGroups.some(
      (item) => item.ratio !== undefined && item.ratio !== null && item.ratio !== "",
    );
    // 一句话展示：只保留有行动意义的状态；其余情形不显示任何徽章，
    // 详细原因收进徽章 hover title，匹配成功才有悬浮分组目录。
    let badgeText = "";
    let badgeTone: BadgeTone = "warning";
    let badgeTitle = "";
    let staleRatio = false;
    const status = binding?.match_status;
    if (siteUnauthenticated) {
      if (hasRatio) {
        staleRatio = true;
      } else {
        badgeText = "站点未登录";
        badgeTitle = siteLoggedOut
          ? upstreamSite?.session_sync_error ||
            "上游站点未登录，请先在站点监控完成浏览器同步"
          : binding?.match_message || "";
      }
    } else if (status === "matched_partial") {
      badgeText = "上游分组不存在";
      badgeTitle = binding?.match_message || "";
    } else if (status === "key_not_found" || status === "no_group") {
      badgeText = "上游 key 未匹配";
      badgeTitle = binding?.match_message || "";
    } else if (status === "needs_key_verification") {
      badgeText = "需要安全验证";
      badgeTitle = binding?.match_message || "";
    } else if (status === "missing_key") {
      badgeText = "缺少渠道 key";
      badgeTitle = binding?.match_message || "";
    }
    const showRatioBadges = !badgeText && hasRatio;
    if (!badgeText && !showRatioBadges) {
      // 兜底：读取失败/推断未命中/从未匹配等其余情形统一一句话，列内永不为空
      badgeText = "上游未匹配";
      badgeTitle = binding?.match_message || "";
    }
    return {
      channel,
      meta: statusMeta(channel.status),
      binding,
      matchedGroups,
      upstreamSite,
      badgeText,
      badgeTone,
      badgeTitle,
      showRatioBadges,
      staleRatio,
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
    // 全量 key / 倍率刷新批次已在后台跑（例如刚在添加弹窗里完成 2FA 验证）：接上进度轮询
    const current = list.find((site) => site.id === siteId.value);
    if (
      current?.key_refresh?.status === "running" ||
      current?.ratio_refresh?.status === "running"
    ) {
      ensureKeyRefreshPolling();
    }
  } catch (err) {
    adminSites.value = [];
    error.value = errorText(err, "主站列表加载失败");
  } finally {
    adminLoading.value = false;
  }
}

// ---- 全量渠道 key 刷新（后台批次 + 进度轮询）----
let keyRefreshTimer: number | null = null;

function stopKeyRefreshPolling() {
  if (keyRefreshTimer != null) {
    window.clearInterval(keyRefreshTimer);
    keyRefreshTimer = null;
  }
}

function ensureKeyRefreshPolling() {
  if (keyRefreshTimer != null) return;
  keyRefreshTimer = window.setInterval(() => void pollKeyRefreshProgress(), 10_000);
}

async function pollKeyRefreshProgress() {
  const targetId = siteId.value;
  if (targetId == null) return;
  try {
    const response = await api.adminSites();
    const list = response.data || [];
    adminSites.value = list;
    const site = list.find((item) => item.id === targetId);
    const keyProgress = site?.key_refresh;
    const ratioProgress = site?.ratio_refresh;
    const activeProgress =
      keyProgress?.status === "running"
        ? keyProgress
        : ratioProgress?.status === "running"
          ? ratioProgress
          : null;
    if (!activeProgress) {
      stopKeyRefreshPolling();
      if (keyProgress?.status === "done") {
        toast.success(
          `渠道 key 全量刷新完成：成功 ${keyProgress.done}/${keyProgress.total}` +
            (keyProgress.failed ? `，失败 ${keyProgress.failed}` : ""),
        );
      }
      if (ratioProgress?.status === "done") {
        toast.success(
          `倍率全量刷新完成：成功 ${ratioProgress.done}/${ratioProgress.total}` +
            (ratioProgress.failed ? `，失败 ${ratioProgress.failed}` : ""),
        );
      }
      if (keyProgress?.status === "done" || ratioProgress?.status === "done") {
        await load("");
      }
      return;
    }
    if (activeProgress.status === "done") {
      stopKeyRefreshPolling();
      await load("");
    }
  } catch {
    // 进度轮询失败不打断页面
  }
}

async function triggerFullRatioRefresh() {
  if (siteId.value == null) return;
  if (securityVerifyNeeded("ratio")) {
    requestSecurityVerify("ratio", triggerFullRatioRefresh);
    return;
  }
  ratioRefreshTriggering.value = true;
  actionError.value = "";
  try {
    const response = await api.refreshAllChannelRatios(siteId.value);
    if (!response.success) throw new Error(response.message || "触发全量倍率刷新失败");
    await pollKeyRefreshProgress();
    if (response.data?.progress?.status === "running" || currentRatioRefresh.value?.status === "running") {
      ensureKeyRefreshPolling();
    }
    toast.info(response.message || "已启动全量倍率刷新");
  } catch (err) {
    const message = errorText(err, "触发全量倍率刷新失败");
    toast.error(message);
  } finally {
    ratioRefreshTriggering.value = false;
  }
}

// ---- 主站 2FA 就地验证：入口拦截 + 验证后续跑 ----
/** NewAPI 主站 proof 缺失（从未验证/已失效）或对应批次已暂停时，先补 2FA 再刷新 */
function securityVerifyNeeded(intent: "key" | "ratio"): boolean {
  const site = currentAdminSite.value;
  if (!site || site.platform !== "newapi") return false;
  const progress = intent === "key" ? currentKeyRefresh.value : currentRatioRefresh.value;
  if (progress?.status === "paused") return true;
  return !site.has_security_proof;
}

function requestSecurityVerify(intent: "key" | "ratio", resume: () => Promise<void>) {
  twoFaIntent.value = intent;
  pendingTwoFaAction = resume;
  twoFaDialogOpen.value = true;
}

async function submitAdminTwoFa(code: string) {
  const targetId = siteId.value;
  if (targetId == null) throw new Error("请先选择主站");
  const result = await api.verifyAdminSiteKeyAccess(targetId, code);
  if (!result.success) {
    throw new Error(result.message || "主站安全验证失败");
  }
  toast.success("主站 2FA 验证通过");
  // 后端验证成功即自动启动/续跑全量 key 批次；刷新主站列表接上最新 proof 与进度，
  // 再按触发时的原始动作续跑（工具栏批次 or 单渠道操作）。
  await loadAdminSites();
  ensureKeyRefreshPolling();
  const resume = pendingTwoFaAction;
  pendingTwoFaAction = null;
  if (resume) {
    await resume();
  }
}

function onTwoFaDialogClose() {
  twoFaDialogOpen.value = false;
  pendingTwoFaAction = null;
}

async function refreshChannelMatches(
  targetSiteId: number,
  channelList: Channel[],
  refreshVersion: number,
): Promise<void> {
  // 主站 key 读取在后端有按站点串行 + 最小间隔保护，前端只需限制并发
  // 避免请求堆积；3 并发在提速与限流之间取平衡。
  const CONCURRENCY = 3;
  const queue = [...channelList];
  const refreshOne = async (channel: Channel): Promise<void> => {
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
  };
  const workers = Array.from(
    { length: Math.min(CONCURRENCY, queue.length) },
    async () => {
      while (queue.length) {
        if (refreshVersion !== loadVersion) return;
        const channel = queue.shift();
        if (!channel) return;
        await refreshOne(channel);
      }
    },
  );
  await Promise.all(workers);
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

// ---- 主站同步：先弹范围选择（全部 / 仅识别 / 勾选渠道）再执行 ----
const SYNCABLE_PLATFORMS = new Set(["newapi", "sub2api"]);
const syncDialogOpen = ref(false);
const syncCandidates = shallowRef<ChannelDiscoveryCandidate[]>([]);
const syncCandidatesLoading = ref(false);

/** 打开同步范围弹窗；sub2api 主站渠道无上游地址可发现，保持直接同步（仅快照） */
async function openSyncDialog() {
  if (siteId.value == null) return;
  if (isSub2Api.value) {
    await runMainSiteSync(siteId.value ?? undefined, "all", []);
    return;
  }
  syncDialogOpen.value = true;
  syncCandidatesLoading.value = true;
  try {
    const response = await api.channelCandidates(siteId.value);
    syncCandidates.value = (response.data || []).filter((candidate) =>
      SYNCABLE_PLATFORMS.has(String(candidate.platform || "unknown")),
    );
  } catch (err) {
    toast.error(errorText(err, "读取可同步渠道失败"));
  } finally {
    syncCandidatesLoading.value = false;
  }
}

async function runMainSiteSync(
  adminSiteId: number | undefined,
  scope: "all" | "recognized" | "selected",
  channelIds: number[],
): Promise<boolean> {
  syncing.value = true;
  try {
    const synced = await handleSyncMainSites(adminSiteId, { scope, channelIds });
    if (!synced) return false;
    groupFilter.value = null;
    selectedChannelId.value = null;
    sub2ApiChannel.value = null;
    rowNote.value = {};
    await load("");
    return true;
  } finally {
    syncing.value = false;
  }
}

function onSyncDialogConfirm(payload: {
  scope: "all" | "recognized" | "selected";
  channelIds: number[];
}) {
  syncDialogOpen.value = false;
  void runMainSiteSync(siteId.value ?? undefined, payload.scope, payload.channelIds);
}

async function matchUpstream(ch: Channel) {
  if (siteId.value == null) return;
  if (securityVerifyNeeded("ratio")) {
    requestSecurityVerify("ratio", () => matchUpstream(ch));
    return;
  }
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
      : "✓ 当前 key 上游倍率已刷新";
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
  if (securityVerifyNeeded("key")) {
    requestSecurityVerify("key", () => refreshChannelKey(ch));
    return;
  }
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
    await load("");
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
  const refreshed = await load("");
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
    await load("");
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
    await load("");
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

async function onPrioritySubmit(priority: number): Promise<void> {
  if (!priorityChannel.value) return;
  return submitForm(priorityChannel.value, priority);
}

async function onSiteIdChange(v: string) {
  siteId.value = Number(v);
}

function onReconcileModeChange(v: string) {
  handleReconcileModeChange(v as ReconcileMode);
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

onUnmounted(() => {
  stopKeyRefreshPolling();
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
            title="选择渠道范围后同步当前主站，并对账消失渠道"
            :disabled="siteId == null"
            :loading="syncing"
            @click="openSyncDialog()"
          >
            <Cloud v-if="!syncing" :size="13" />
            同步主站
          </Button>
          <Button
            variant="secondary"
            aria-label="刷新全部倍率"
            title="并发重新匹配当前主站全部渠道的上游分组倍率（复用已保存 key，无需 2FA）"
            :disabled="siteId == null || ratioRefreshTriggering"
            :loading="ratioRefreshTriggering"
            @click="triggerFullRatioRefresh"
          >
            <Percent v-if="!ratioRefreshTriggering" :size="13" />
            刷新倍率
          </Button>
          <span
            v-if="currentKeyRefresh"
            :class="[
              'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold tabular-nums',
              currentKeyRefresh.status === 'running'
                ? 'bg-sunken text-ink-muted'
                : currentKeyRefresh.status === 'paused' || currentKeyRefresh.status === 'failed'
                  ? 'bg-danger-bg text-danger-fg'
                  : 'bg-success-bg text-success-fg',
            ]"
            :title="currentKeyRefresh.message || undefined"
          >
            {{ keyRefreshStatusText }}
          </span>
          <span
            v-if="currentRatioRefresh"
            :class="[
              'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold tabular-nums',
              currentRatioRefresh.status === 'running'
                ? 'bg-sunken text-ink-muted'
                : currentRatioRefresh.status === 'paused' || currentRatioRefresh.status === 'failed'
                  ? 'bg-danger-bg text-danger-fg'
                  : 'bg-success-bg text-success-fg',
            ]"
            :title="currentRatioRefresh.message || undefined"
          >
            {{ ratioRefreshStatusText }}
          </span>
          <span class="mx-1 h-5 w-px bg-line-strong" aria-hidden="true" />
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
          <div
            class="priceai-scrollbar space-y-2 lg:max-h-[calc(100vh-19.5rem)] lg:overflow-y-auto"
            @dragover="onGroupListDragOver"
            @dragleave="onGroupListDragLeave"
            @drop="onGroupListDrop"
          >
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
          <template v-for="(name, index) in groupRows" :key="name">
            <div
              v-if="dragGroupName && dragInsertIndex === index"
              class="rounded-[var(--radius-sm)] border border-dashed border-[var(--color-accent)] bg-sunken"
              :style="{ height: `${dragPlaceholderHeight}px` }"
              aria-hidden="true"
            />
            <button
              draggable="true"
              data-group-card
              :class="[
                'w-full cursor-grab rounded-[var(--radius-sm)] border px-3 py-2 text-left transition active:cursor-grabbing',
                groupFilter === name
                  ? 'border-[var(--color-accent)] bg-sunken'
                  : 'border-line hover:bg-sunken-hover',
                dragGroupName === name ? 'opacity-40' : '',
              ]"
              @dragstart="onGroupDragStart(name, $event)"
              @dragend="clearGroupDragState"
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
          </template>
          <div
            v-if="dragGroupName && dragInsertIndex !== null && dragInsertIndex >= groupRows.length"
            class="rounded-[var(--radius-sm)] border border-dashed border-[var(--color-accent)] bg-sunken"
            :style="{ height: `${dragPlaceholderHeight}px` }"
            aria-hidden="true"
          />
          </div>
        </aside>

        <!-- 渠道主区域 -->
        <div class="min-w-0">
          <div v-if="loading" class="space-y-2 py-1" aria-hidden="true">
            <div v-for="i in 6" :key="i" class="skeleton h-[52px] w-full rounded-[var(--radius-sm)]" />
          </div>
          <EmptyState
            v-else-if="!visibleChannels.length"
            dense
            :title="groupFilter ? `分组「${groupFilter}」下暂无渠道` : '主站当前没有可显示的渠道'"
            :description="groupFilter ? '换个分组看看，或点上方「清除」回到全部渠道。' : '点右上角「同步主站」从上游拉取渠道，或检查筛选条件。'"
          />
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
                  <th class="pl-3 pb-2">渠道</th>
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
                  <td class="max-w-0 py-3 pl-3 pr-3">
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
                    <UpstreamGroupsPopover
                      v-if="row.showRatioBadges && !row.staleRatio"
                      :site="row.upstreamSite"
                      :matched-groups="row.matchedGroups"
                    >
                      <div class="flex max-w-full flex-wrap gap-1 overflow-hidden">
                        <Badge
                          v-for="item in row.matchedGroups"
                          :key="item.name"
                          :tone="item.available_to_login === false ? 'warning' : 'success'"
                          class="max-w-full truncate"
                          :title="item.name"
                        >
                          {{ ratioXText(item) }}
                        </Badge>
                      </div>
                    </UpstreamGroupsPopover>
                    <div
                      v-else-if="row.showRatioBadges && row.staleRatio"
                      class="flex max-w-full flex-wrap gap-1 overflow-hidden"
                    >
                      <Badge
                        v-for="item in row.matchedGroups"
                        :key="item.name"
                        tone="neutral"
                        class="max-w-full truncate border border-dashed"
                        :title="`站点未登录，显示上次成功倍率 · ${item.name}`"
                      >
                        {{ ratioXText(item) }}
                      </Badge>
                    </div>
                    <Badge
                      v-else-if="row.badgeText"
                      :tone="row.badgeTone"
                      dot
                      class="max-w-full truncate"
                      :title="row.badgeTitle"
                    >
                      {{ row.badgeText }}
                    </Badge>
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
                  <td class="py-3 pr-3">
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
                {{ ratioXText(item) }}
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

    <AdminSiteFormDialog
      :open="adminFormOpen"
      :site="editingAdmin"
      @close="adminFormOpen = false"
      :on-saved="loadAdminSites"
    />

    <AdminTwoFaDialog
      :open="twoFaDialogOpen"
      :site-name="currentAdminSite?.name"
      :intent="twoFaIntent"
      :on-submit="submitAdminTwoFa"
      @close="onTwoFaDialogClose"
    />

    <SyncScopeDialog
      :open="syncDialogOpen"
      :site-label="
        currentAdminSite
          ? `${currentAdminSite.platform_label || 'NewAPI'} · ${currentAdminSite.name}`
          : ''
      "
      :candidates-loading="syncCandidatesLoading"
      :candidates="syncCandidates"
      @close="syncDialogOpen = false"
      @confirm="onSyncDialogConfirm"
    />
  </div>
</template>
