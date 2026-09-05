<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  ChevronDown,
  Eye,
  Gauge,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  Trash2,
} from "lucide-vue-next";
import type { SessionSyncStatus, Site } from "@/lib/types";
import {
  fmtTimeParts,
  platformLabel,
  sessionFailureBadge,
  siteStatusLabel,
  siteStatusTone,
  truthy,
} from "@/lib/format";
import Badge from "@/components/Badge.vue";
import { Button, EmptyState } from "@/components/ui";

interface Props {
  sites: Site[];
  selectedId?: number | null;
  groupByPlatform?: boolean;
  /** 总览精简模式：只保留 状态 / 登录态 / 上次检测，无操作列 */
  compact?: boolean;
}
const props = withDefaults(defineProps<Props>(), {
  selectedId: null,
  groupByPlatform: false,
  compact: false,
});

const emit = defineEmits<{
  view: [site: Site];
  ratios: [site: Site];
  check: [site: Site];
  edit: [site: Site];
  delete: [site: Site];
  syncSession: [site: Site];
}>();

const PLATFORM_SECTION: Record<
  string,
  { label: string; tone: "success" | "info" }
> = {
  newapi: { label: "NewAPI 渠道", tone: "success" },
  sub2api: { label: "sub2api 渠道", tone: "info" },
};

// 折叠的分组平台（按平台分组时每个平台可独立收起）
const collapsedPlatforms = ref(new Set<string>());
// 当前检测中的渠道 id（按钮转圈）
const checkingId = ref<number | null>(null);
// 当前同步登录态中的渠道 id
const syncingId = ref<number | null>(null);

// 「更多」下拉：同一时刻只展开一行
const openMenuId = ref<number | null>(null);
const menuContainers = new Map<number, HTMLElement>();

function togglePlatform(key: string) {
  const next = new Set(collapsedPlatforms.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  collapsedPlatforms.value = next;
}

function isPlatformCollapsed(key: string): boolean {
  return collapsedPlatforms.value.has(key);
}

function toggleMenu(id: number) {
  openMenuId.value = openMenuId.value === id ? null : id;
}

function closeMenu() {
  openMenuId.value = null;
}

function setMenuContainer(el: unknown, id: number) {
  if (el instanceof HTMLElement) {
    menuContainers.set(id, el);
  } else {
    menuContainers.delete(id);
  }
}

function onDocMouseDown(e: MouseEvent) {
  if (openMenuId.value === null) return;
  const target = e.target as Node | null;
  if (!target) return;
  const container = menuContainers.get(openMenuId.value);
  if (container && !container.contains(target)) {
    openMenuId.value = null;
  }
}

function onDocKeyDown(e: KeyboardEvent) {
  if (e.key === "Escape") openMenuId.value = null;
}

onMounted(() => {
  document.addEventListener("mousedown", onDocMouseDown);
  document.addEventListener("keydown", onDocKeyDown);
});
onUnmounted(() => {
  document.removeEventListener("mousedown", onDocMouseDown);
  document.removeEventListener("keydown", onDocKeyDown);
});

function handleCheck(site: Site) {
  checkingId.value = site.id;
  emit("check", site);
}

function handleSyncSession(site: Site) {
  syncingId.value = site.id;
  emit("syncSession", site);
}

// emit 不返回 Promise，无法 await 父组件的异步回调；
// 父组件完成检测/同步后会刷新 sites，借此清除行内 loading。
watch(
  () => props.sites,
  () => {
    checkingId.value = null;
    syncingId.value = null;
  },
);

function hiddenCount(site: Site): number {
  const authCount = Number(site.current_login_groups_count || 0);
  const publicCount = Number(site.current_groups_count || 0);
  return Math.max(0, authCount - publicCount);
}

function timeParts(value?: string | null): [string, string] {
  return fmtTimeParts(value);
}

function sessionSyncLabel(status?: SessionSyncStatus): string {
  return {
    not_requested: "待同步",
    pending: "等待扩展",
    validating: "验证中",
    ready: "登录态已同步",
    no_session: "没有登录态",
    expired: "登录态已失效",
    permission_required: "需要站点权限",
    extension_unavailable: "扩展未连接",
    failed: "同步失败",
  }[status || "not_requested"];
}

function sessionSyncTone(
  status?: SessionSyncStatus,
): "success" | "warning" | "danger" | "info" {
  if (status === "ready") return "success";
  // not_requested = 未配置登录态，属中性信息，不渲染成红色「失效」(P1-1)
  if (
    status === "pending" ||
    status === "validating" ||
    status === "not_requested"
  ) {
    return "info";
  }
  if (status === "no_session" || status === "permission_required") {
    return "warning";
  }
  return "danger";
}

const hasSites = computed(() => props.sites.length > 0);

const sections = computed(() => {
  if (props.groupByPlatform) {
    return (["newapi", "sub2api"] as const)
      .map((key) => ({
        key,
        label: PLATFORM_SECTION[key].label,
        tone: PLATFORM_SECTION[key].tone,
        sites: props.sites.filter((site) => site.platform === key),
      }))
      .filter((section) => section.sites.length > 0);
  }
  return [
    { key: "all" as const, label: "", tone: "info" as const, sites: props.sites },
  ];
});
</script>

<template>
  <EmptyState
    v-if="!hasSites"
    title="还没有渠道"
    description="从右上角「添加渠道」开始，先接入一个 NewAPI / sub2api 上游站点，再决定抓取频率和认证方式。"
  />
  <div v-else class="priceai-scrollbar min-w-0 overflow-x-auto pb-1">
    <table class="w-full min-w-max table-auto text-left text-[13px]">
      <thead>
        <tr
          class="border-b border-line text-[11.5px] font-semibold tracking-[0.04em] text-ink-soft uppercase"
        >
          <th class="whitespace-nowrap pb-2.5 pr-3">渠道</th>
          <th class="whitespace-nowrap pb-2.5 pr-3">状态</th>
          <th v-if="compact" class="whitespace-nowrap pb-2.5 pr-3">登录态</th>
          <th v-else class="whitespace-nowrap pb-2.5 pr-3">认证 / 隐藏</th>
          <th v-if="!compact" class="whitespace-nowrap pb-2.5 pr-3">分组</th>
          <th class="whitespace-nowrap pb-2.5 pr-3">上次检测</th>
          <th v-if="!compact" class="whitespace-nowrap pb-2.5">操作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="section in sections" :key="section.key">
          <tr v-if="groupByPlatform">
            <td :colspan="compact ? 4 : 6" class="pt-4 first:pt-0">
              <button
                type="button"
                :class="[
                  'group/section flex w-full items-center justify-between rounded-[var(--radius-sm)] border border-line bg-panel-soft px-3 py-2 text-left text-[12px] outline-none transition-[border-color,background-color] duration-[var(--motion-base)] hover:border-line-strong',
                  section.tone === 'success'
                    ? 'data-[tone=success]:bg-success-bg'
                    : '',
                ]"
                :aria-expanded="!isPlatformCollapsed(section.key)"
                @click="togglePlatform(section.key)"
              >
                <span
                  class="flex items-center gap-2 font-semibold text-ink-strong"
                >
                  <ChevronDown
                    :size="13"
                    :class="[
                      'transition-transform duration-[var(--motion-base)]',
                      isPlatformCollapsed(section.key)
                        ? '-rotate-90'
                        : 'rotate-0',
                    ]"
                    aria-hidden="true"
                  />
                  {{ section.label }}
                </span>
                <span
                  class="rounded-[var(--radius-pill)] border border-line bg-panel px-2 py-0.5 tabular text-[11.5px] font-medium text-ink-muted"
                >
                  {{ section.sites.length }} 个
                </span>
              </button>
            </td>
          </tr>
          <template
            v-if="!groupByPlatform || !isPlatformCollapsed(section.key)"
          >
            <tr
              v-for="site in section.sites"
              :key="site.id"
              :class="[
                'group border-b border-line-soft transition-colors duration-[var(--motion-fast)] last:border-0 hover:bg-sunken-hover',
                site.id === selectedId ? 'bg-sunken' : '',
              ]"
            >
              <td class="min-w-0 py-3 pr-3 align-middle">
                <div class="flex items-center gap-3">
                  <span
                    :class="[
                      'h-9 w-[2px] shrink-0 rounded-full transition-colors duration-[var(--motion-fast)]',
                      site.id === selectedId
                        ? 'bg-accent'
                        : 'bg-transparent group-hover:bg-line-strong',
                    ]"
                    aria-hidden="true"
                  />
                  <div class="min-w-0">
                    <div class="font-semibold text-ink-strong">
                      {{ site.name }}
                    </div>
                    <div
                      class="mt-0.5 truncate font-mono text-[11px] text-ink-soft"
                    >
                      {{ platformLabel(site) }} · {{ site.base_url }}
                    </div>
                  </div>
                </div>
              </td>
              <td class="py-3 pr-3 align-middle">
                <Badge :tone="siteStatusTone(site)" dot>
                  {{ siteStatusLabel(site) }}
                </Badge>
              </td>
              <td v-if="compact" class="py-3 pr-3 align-middle">
                <div class="flex flex-wrap gap-1">
                  <Badge
                    v-if="sessionFailureBadge(site)"
                    :tone="sessionFailureBadge(site)!.tone"
                    :title="sessionFailureBadge(site)!.title"
                    >{{ sessionFailureBadge(site)!.label }}</Badge
                  >
                  <template v-if="site.platform === 'newapi'">
                    <Badge
                      v-if="site.auth_mode === 'browser'"
                      :tone="sessionSyncTone(site.session_sync_status)"
                      >{{ sessionSyncLabel(site.session_sync_status) }}</Badge
                    >
                    <Badge
                      v-else-if="site.access_user_id && site.has_access_token"
                      tone="info"
                      >用户登录</Badge
                    >
                    <Badge
                      v-else
                      tone="warning"
                      title="请在「更多 → 编辑渠道」中开启认证增强"
                      >未登录</Badge
                    >
                  </template>
                  <Badge
                    v-else-if="site.auth_mode === 'browser'"
                    :tone="sessionSyncTone(site.session_sync_status)"
                    >{{ sessionSyncLabel(site.session_sync_status) }}</Badge
                  >
                  <Badge v-else-if="site.platform === 'sub2api'" tone="info"
                    >用户登录</Badge
                  >
                  <Badge v-else-if="truthy(site.login_enabled)" tone="info"
                    >认证增强</Badge
                  >
                </div>              </td>
              <td v-else class="py-3 pr-3 align-middle">
                <div class="flex flex-wrap gap-1">
                  <Badge
                    v-if="sessionFailureBadge(site)"
                    :tone="sessionFailureBadge(site)!.tone"
                    :title="sessionFailureBadge(site)!.title"
                    >{{ sessionFailureBadge(site)!.label }}</Badge
                  >
                  <Badge v-if="hiddenCount(site) > 0" tone="warning">
                    {{ hiddenCount(site) }} 隐藏
                  </Badge>
                  <span v-else class="text-[11.5px] text-ink-faint">无</span>
                  <template v-if="site.platform === 'newapi'">
                    <Badge
                      v-if="site.auth_mode === 'browser'"
                      :tone="sessionSyncTone(site.session_sync_status)"
                      >{{ sessionSyncLabel(site.session_sync_status) }}</Badge
                    >
                    <Badge
                      v-else-if="site.access_user_id && site.has_access_token"
                      tone="info"
                      >用户登录</Badge
                    >
                    <Badge
                      v-else
                      tone="warning"
                      title="请在「更多 → 编辑渠道」中开启认证增强，支持浏览器登录态同步、系统访问令牌或账号密码登录"
                      >未登录，需要配置登录</Badge
                    >
                  </template>
                  <Badge
                    v-else-if="site.auth_mode === 'browser'"
                    :tone="sessionSyncTone(site.session_sync_status)"
                    >{{ sessionSyncLabel(site.session_sync_status) }}</Badge
                  >
                  <Badge v-else-if="site.platform === 'sub2api'" tone="info"
                    >用户登录</Badge
                  >
                  <Badge v-else-if="truthy(site.login_enabled)" tone="info"
                    >认证增强</Badge
                  >
                </div>              </td>
              <td
                v-if="!compact"
                class="py-3 pr-3 align-middle tabular text-ink-strong"
              >
                {{ site.current_groups_count || 0 }}
              </td>
              <td class="py-3 pr-3 align-middle text-[11.5px] text-ink-muted">
                <span class="whitespace-nowrap tabular leading-[1.35]">
                  <span class="block">{{
                    timeParts(site.last_check_at)[0]
                  }}</span>
                  <span
                    v-if="timeParts(site.last_check_at)[1]"
                    class="block text-ink-soft"
                    >{{ timeParts(site.last_check_at)[1] }}</span
                  >
                </span>
              </td>
              <td v-if="!compact" class="py-3 align-middle">
                <div class="flex flex-nowrap items-center justify-end gap-1">
                  <Button
                    v-if="
                      site.auth_mode === 'browser' &&
                      !['ready', 'pending', 'validating'].includes(
                        site.session_sync_status || 'not_requested',
                      )
                    "
                    variant="secondary"
                    size="sm"
                    class="shrink-0"
                    aria-label="同步登录态"
                    title="从浏览器同步登录态"
                    :loading="syncingId === site.id"
                    @click="handleSyncSession(site)"
                  >
                    <RefreshCw v-if="syncingId !== site.id" :size="12" />
                    同步
                  </Button>
                  <Button
                    v-else-if="
                      site.platform === 'sub2api' &&
                      site.auth_mode !== 'browser'
                    "
                    variant="secondary"
                    size="sm"
                    class="shrink-0"
                    aria-label="同步凭证"
                    :title="
                      site.auth_mode === 'token' ? '更新 Token' : '更新账号密码'
                    "
                    @click="emit('edit', site)"
                  >
                    <Pencil :size="12" />
                    同步
                  </Button>
                  <Button
                    variant="brand"
                    size="sm"
                    class="shrink-0"
                    aria-label="查看倍率"
                    title="查看倍率"
                    @click="emit('ratios', site)"
                  >
                    <Gauge :size="12" />
                    倍率
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    class="shrink-0"
                    aria-label="立即检测"
                    title="立即检测"
                    :loading="checkingId === site.id"
                    @click="handleCheck(site)"
                  >
                    <RefreshCw v-if="checkingId !== site.id" :size="12" />
                    检测
                  </Button>
                  <div
                    :ref="(el) => setMenuContainer(el, site.id)"
                    class="relative shrink-0"
                  >
                    <button
                      type="button"
                      aria-label="更多操作"
                      title="更多"
                      aria-haspopup="menu"
                      :aria-expanded="openMenuId === site.id"
                      class="inline-flex h-7 w-7 items-center justify-center rounded-[var(--radius-sm)] border border-line bg-panel text-ink-muted transition-[border-color,color] duration-[var(--motion-fast)] hover:border-line-strong hover:text-ink-strong"
                      @click="toggleMenu(site.id)"
                    >
                      <MoreHorizontal :size="14" />
                    </button>
                    <div
                      v-if="openMenuId === site.id"
                      role="menu"
                      class="absolute right-0 top-8 z-20 min-w-[140px] overflow-hidden rounded-[var(--radius-md)] border border-line bg-panel py-1 shadow-[var(--shadow-floating)]"
                    >
                      <button
                        type="button"
                        role="menuitem"
                        class="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] transition-colors duration-[var(--motion-fast)] text-ink hover:bg-sunken-hover"
                        @click="closeMenu(); emit('view', site)"
                      >
                        <span class="shrink-0 text-ink-muted" aria-hidden="true">
                          <Eye :size="13" />
                        </span>
                        查看详情
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        class="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] transition-colors duration-[var(--motion-fast)] text-ink hover:bg-sunken-hover"
                        @click="closeMenu(); emit('edit', site)"
                      >
                        <span class="shrink-0 text-ink-muted" aria-hidden="true">
                          <Pencil :size="13" />
                        </span>
                        编辑渠道
                      </button>
                      <div
                        class="my-1 border-t border-line-soft"
                        aria-hidden="true"
                      />
                      <button
                        type="button"
                        role="menuitem"
                        class="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] transition-colors duration-[var(--motion-fast)] text-danger-fg hover:bg-danger-bg"
                        @click="closeMenu(); emit('delete', site)"
                      >
                        <span class="shrink-0 text-danger-fg" aria-hidden="true">
                          <Trash2 :size="13" />
                        </span>
                        删除
                      </button>
                    </div>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </template>
      </tbody>
    </table>
  </div>
</template>
