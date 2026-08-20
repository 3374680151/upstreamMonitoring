import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Cloud, KeyRound, RefreshCw } from "lucide-react";
import { AdminSiteFormDialog } from "@/components/AdminSiteFormDialog";
import { Badge } from "@/components/Badge";
import { ChannelPriorityDialog } from "@/components/ChannelPriorityDialog";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { Sub2ApiChannelDialog } from "@/components/Sub2ApiChannelDialog";
import { Sub2ApiChannelTable } from "@/components/Sub2ApiChannelTable";
import { errorText, useToast } from "@/components/Toast";
import { Button, Input, Select, Spinner } from "@/components/ui";
import { claimAutomaticRefresh } from "@/lib/automaticRefresh";
import { api } from "@/lib/api";
import { normalizedChannelStatus } from "@/lib/sub2apiChannel";
import { explainUpstreamError } from "@/lib/upstreamError";
import type {
  AdminSite,
  Channel,
  ChannelUpstreamBinding,
  GroupItem,
  Sub2ApiGroupRef,
} from "@/lib/types";

type ReconcileMode = "disable" | "delete";

const STATUS_META: Record<
  number,
  { label: string; tone: "success" | "warning" | "danger" | "neutral" }
> = {
  1: { label: "启用", tone: "success" },
  2: { label: "手动停用", tone: "warning" },
  3: { label: "自动停用", tone: "danger" },
};

function statusMeta(status?: number | string) {
  return STATUS_META[Number(status)] || {
    label: `未知(${status})`,
    tone: "neutral" as const,
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

function bindingFailure(binding?: ChannelUpstreamBinding) {
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

function bindingTone(
  status?: string,
): "success" | "warning" | "danger" | "neutral" {
  if (status === "matched") return "success";
  if (status === "matched_partial") return "warning";
  if (status === "needs_key_verification") return "warning";
  if (status && status !== "unmatched") return "danger";
  return "neutral";
}

export function ChannelsPage({
  reconcileMode,
  onReconcileModeChange,
  onSyncMainSites,
  syncing,
}: {
  reconcileMode: ReconcileMode;
  onReconcileModeChange: (mode: ReconcileMode) => void;
  onSyncMainSites: (adminSiteId: number) => boolean | Promise<boolean>;
  syncing: boolean;
}) {
  const toast = useToast();
  const [adminSites, setAdminSites] = useState<AdminSite[]>([]);
  const [siteId, setSiteId] = useState<number | null>(null);
  const [adminLoading, setAdminLoading] = useState(true);
  const [adminFormOpen, setAdminFormOpen] = useState(false);
  const [editingAdmin, setEditingAdmin] = useState<AdminSite | null>(null);

  const [channels, setChannels] = useState<Channel[]>([]);
  const [groups, setGroups] = useState<Record<string, GroupItem>>({});
  const [groupCatalogReady, setGroupCatalogReady] = useState(false);
  const [upstreamBindings, setUpstreamBindings] = useState<
    Record<string, ChannelUpstreamBinding>
  >({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [staleDataWarning, setStaleDataWarning] = useState("");
  const [keyword, setKeyword] = useState("");
  const [groupFilter, setGroupFilter] = useState<string | null>(null);
  const [selectedChannelId, setSelectedChannelId] = useState<number | null>(null);
  const [matching, setMatching] = useState<Set<number>>(() => new Set());
  const [refreshingKeyIds, setRefreshingKeyIds] = useState<Set<number>>(
    () => new Set(),
  );
  const [rowNote, setRowNote] = useState<
    Record<number, { ok: boolean; text: string }>
  >({});
  const [priorityChannel, setPriorityChannel] = useState<Channel | null>(null);
  const [sub2ApiChannel, setSub2ApiChannel] = useState<Channel | null>(null);
  const [updatingChannelIds, setUpdatingChannelIds] = useState<Set<number>>(
    () => new Set(),
  );
  const [actionError, setActionError] = useState("");
  const loadVersion = useRef(0);
  const automaticallyRefreshedSiteIds = useRef(new Set<number>());
  const loadedSiteId = useRef<number | null>(null);
  const dataSiteId = useRef<number | null>(null);

  const currentAdminSite = useMemo(
    () => adminSites.find((site) => site.id === siteId) || null,
    [adminSites, siteId],
  );
  const isSub2Api = currentAdminSite?.platform === "sub2api";

  const loadAdminSites = useCallback(async () => {
    setAdminLoading(true);
    try {
      const response = await api.adminSites();
      const list = response.data || [];
      setAdminSites(list);
      setError("");
      setSiteId((previous) => {
        if (previous && list.some((site) => site.id === previous)) return previous;
        return list[0]?.id ?? null;
      });
    } catch (err) {
      setAdminSites([]);
      setError(errorText(err, "主站列表加载失败"));
    } finally {
      setAdminLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAdminSites();
  }, [loadAdminSites]);

  const refreshChannelMatches = useCallback(
    async (
      targetSiteId: number,
      channelList: Channel[],
      refreshVersion: number,
    ) => {
      for (const channel of channelList) {
        if (refreshVersion !== loadVersion.current) return;
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
        if (refreshVersion !== loadVersion.current) return;
        setUpstreamBindings((previous) => {
          const previousBinding = previous[String(channel.id)];
          if (
            previousBinding?.matched_groups?.length &&
            !data.matched_groups?.length &&
            isStaleMatchStatus(data.match_status)
          ) {
            return {
              ...previous,
              [String(channel.id)]: {
                ...previousBinding,
                match_status: "refresh_error",
                match_message: data.match_message,
              },
            };
          }
          return { ...previous, [String(channel.id)]: data };
        });
      }
    },
    [],
  );

  const load = useCallback(
    async (
      query: string,
      options: { refreshMatches?: boolean; waitForMatches?: boolean } = {},
    ) => {
      if (siteId == null || !currentAdminSite) return false;
      const targetSiteId = siteId;
      const targetPlatform = currentAdminSite.platform;
      const refreshVersion = ++loadVersion.current;
      setLoading(true);
      setError("");
      setStaleDataWarning("");
      try {
        const [channelResponse, groupResponse, bindingResponse] =
          targetPlatform === "sub2api"
            ? await Promise.all([
                api.channels(targetSiteId, query),
                api.channelGroups(targetSiteId),
                Promise.resolve({ success: true, data: {} }),
              ])
            : await Promise.all([
                api.channels(targetSiteId, query),
                api
                  .channelGroups(targetSiteId)
                  .catch(() => ({ success: false, data: {} })),
                api
                  .channelUpstreamBindings(targetSiteId)
                  .catch(() => ({ success: false, data: {} })),
              ]);
        if (refreshVersion !== loadVersion.current) return false;
        setChannels(channelResponse.data || []);
        setGroups(groupResponse.data || {});
        setGroupCatalogReady(groupResponse.success === true);
        setUpstreamBindings(bindingResponse.data || {});
        dataSiteId.current = targetSiteId;
        setRowNote({});
        setActionError("");
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
        if (refreshVersion !== loadVersion.current) return false;
        const message = errorText(err, "主站渠道加载失败");
        if (dataSiteId.current === targetSiteId) {
          setStaleDataWarning(`刷新失败，当前显示上次成功数据：${message}`);
        } else {
          setChannels([]);
          setGroups({});
          setGroupCatalogReady(false);
          setUpstreamBindings({});
          setError(message);
        }
        return false;
      } finally {
        if (refreshVersion === loadVersion.current) setLoading(false);
      }
    },
    [siteId, currentAdminSite, refreshChannelMatches],
  );

  useEffect(() => {
    setGroupFilter(null);
    setSelectedChannelId(null);
    setSub2ApiChannel(null);
    setRowNote({});
    setMatching(new Set());
    setUpdatingChannelIds(new Set());
    setStaleDataWarning("");
    if (siteId == null) {
      loadedSiteId.current = null;
      dataSiteId.current = null;
      setChannels([]);
      setGroups({});
      setUpstreamBindings({});
      return;
    }
    if (loadedSiteId.current === siteId) return;
    loadedSiteId.current = siteId;
    dataSiteId.current = null;
    setChannels([]);
    setGroups({});
    setGroupCatalogReady(false);
    setUpstreamBindings({});
    const refreshMatches =
      currentAdminSite?.platform === "newapi" &&
      claimAutomaticRefresh(automaticallyRefreshedSiteIds.current, siteId);
    void load("", { refreshMatches });
  }, [siteId, currentAdminSite?.platform, load]);

  async function syncCurrentMainSite() {
    if (siteId == null) return;
    const synced = await onSyncMainSites(siteId);
    if (!synced) return;
    setGroupFilter(null);
    setSelectedChannelId(null);
    setSub2ApiChannel(null);
    setRowNote({});
    await load("", { refreshMatches: true, waitForMatches: true });
  }

  const allowedGroupNames = useMemo(
    () => (groupCatalogReady ? new Set(Object.keys(groups)) : null),
    [groupCatalogReady, groups],
  );

  const groupChannelCount = useMemo(() => {
    const count: Record<string, number> = {};
    for (const channel of channels) {
      for (const group of channelGroupNames(channel, allowedGroupNames)) {
        count[group] = (count[group] || 0) + 1;
      }
    }
    return count;
  }, [allowedGroupNames, channels]);

  const groupRows = useMemo(() => {
    if (allowedGroupNames) return Array.from(allowedGroupNames).sort();
    const names = new Set<string>([
      ...Object.keys(groups),
      ...Object.keys(groupChannelCount),
    ]);
    return Array.from(names).sort();
  }, [allowedGroupNames, groups, groupChannelCount]);

  const visibleChannels = useMemo(() => {
    if (!groupFilter) return channels;
    return channels.filter((channel) =>
      channelGroupNames(channel, allowedGroupNames).includes(groupFilter),
    );
  }, [allowedGroupNames, channels, groupFilter]);

  const sub2ApiGroups = useMemo(
    () =>
      Object.values(groups).flatMap((group): Sub2ApiGroupRef[] => {
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
    [groups],
  );

  const selectedChannel = useMemo(
    () => channels.find((channel) => channel.id === selectedChannelId) || null,
    [channels, selectedChannelId],
  );
  const selectedBinding = selectedChannel
    ? upstreamBindings[String(selectedChannel.id)]
    : undefined;
  const selectedBindingStale = isStaleMatchStatus(selectedBinding?.match_status);
  const selectedBindingError = bindingFailure(selectedBinding);

  async function matchUpstream(ch: Channel) {
    if (siteId == null) return;
    setMatching((previous) => new Set(previous).add(ch.id));
    setActionError("");
    try {
      const response = await api.matchChannelUpstreamBinding(siteId!, ch.id);
      if (!response.success && !response.data) {
        throw new Error(response.message || "匹配失败");
      }
      const binding = response.data || {};
      setUpstreamBindings((previous) => ({
        ...previous,
        [String(ch.id)]: binding,
      }));
      const staleMatch = isStaleMatchStatus(binding.match_status);
      const explained = staleMatch
        ? bindingFailure(binding)
        : null;
      const note = staleMatch
        ? binding.matched_groups?.length
          ? `${staleMatchPrefix(binding)} · 错误原因：${explained!.summary}`
          : `${bindingStatusLabel(binding)}：${explained!.summary}`
        : currentKeySummary(binding);
      setRowNote((previous) => ({
        ...previous,
        [ch.id]: { ok: response.success && !staleMatch, text: note },
      }));
      if (staleMatch) {
        if (binding.matched_groups?.length) {
          toast.info(
            `渠道「${ch.name || `#${ch.id}`}」${staleMatchPrefix(binding)}：${explained!.summary}`,
          );
        } else {
          setActionError(
            `上游匹配失败：${explained!.summary}。原始错误：${explained!.raw}`,
          );
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
      const previousBinding = upstreamBindings[String(ch.id)];
      if (previousBinding?.matched_groups?.length) {
        setUpstreamBindings((previous) => ({
          ...previous,
          [String(ch.id)]: {
            ...previousBinding,
            match_status: "refresh_error",
            match_message: message,
          },
        }));
        setRowNote((previous) => ({
          ...previous,
          [ch.id]: { ok: true, text: "刷新失败，继续显示上次成功倍率" },
        }));
        toast.info(
          `渠道「${ch.name || `#${ch.id}`}」刷新失败：${explained.summary}，继续显示上次成功倍率`,
        );
      } else {
        setRowNote((previous) => ({
          ...previous,
          [ch.id]: { ok: false, text: `刷新失败：${explained.summary}` },
        }));
        setActionError(
          `上游匹配失败：${explained.summary}。原始错误：${explained.raw}`,
        );
        toast.error(
          `渠道「${ch.name || `#${ch.id}`}」刷新失败：${explained.summary}`,
        );
      }
    } finally {
      setMatching((previous) => {
        const next = new Set(previous);
        next.delete(ch.id);
        return next;
      });
    }
  }

  async function refreshChannelKey(ch: Channel) {
    if (siteId == null || refreshingKeyIds.size > 0) return;
    setRefreshingKeyIds(new Set([ch.id]));
    setActionError("");
    try {
      const response = await api.refreshChannelKey(siteId, ch.id);
      const data = response.data;
      if (!response.success || !data) {
        throw new Error(response.message || "渠道 key 刷新失败");
      }
      if (data.binding) {
        setUpstreamBindings((previous) => ({
          ...previous,
          [String(ch.id)]: data.binding!,
        }));
      }
      const keyMessage = data.first_fetch
        ? "真实 key 已保存"
        : data.changed
          ? "key 已更新"
          : "key 已是最新";
      const text = data.match_success
        ? `${keyMessage}，倍率已刷新`
        : `${keyMessage}，但倍率刷新失败：${data.match_message || "未知错误"}`;
      setRowNote((previous) => ({
        ...previous,
        [ch.id]: { ok: data.match_success, text },
      }));
      if (data.match_success) toast.success(`渠道「${ch.name || `#${ch.id}`}」${text}`);
      else toast.info(`渠道「${ch.name || `#${ch.id}`}」${text}`);
    } catch (err) {
      const message = errorText(err, "渠道 key 刷新失败");
      const explained = explainUpstreamError(message);
      setRowNote((previous) => ({
        ...previous,
        [ch.id]: { ok: false, text: `刷新 key 失败：${explained.summary}` },
      }));
      setActionError(
        `渠道「${ch.name || `#${ch.id}`}」刷新 key 失败：${explained.summary}。原始错误：${explained.raw}`,
      );
      toast.error(`渠道「${ch.name || `#${ch.id}`}」刷新 key 失败：${explained.summary}`);
    } finally {
      setRefreshingKeyIds(new Set());
    }
  }

  async function submitForm(channel: Channel, priority: number) {
    setActionError("");
    try {
      const response = await api.updateChannel(siteId!, channel.id, { priority });
      if (!response.success) {
        throw new Error(response.message || "优先级保存失败");
      }
      await load(keyword);
      toast.success(`渠道「${channel.name || `#${channel.id}`}」优先级已更新`);
    } catch (err) {
      const message = errorText(err, "优先级保存失败");
      setActionError(`优先级保存失败：${message}`);
      toast.error(`优先级保存失败：${message}`);
      throw new Error(message);
    }
  }

  function setChannelUpdating(channelId: number, updating: boolean) {
    setUpdatingChannelIds((previous) => {
      const next = new Set(previous);
      if (updating) next.add(channelId);
      else next.delete(channelId);
      return next;
    });
  }

  async function refreshSub2ApiChannel(channel: Channel) {
    setChannelUpdating(channel.id, true);
    const refreshed = await load(keyword);
    setChannelUpdating(channel.id, false);
    if (refreshed) {
      toast.success(`渠道「${channel.name || `#${channel.id}`}」配置已刷新`);
    } else {
      toast.error(`渠道「${channel.name || `#${channel.id}`}」刷新失败`);
    }
  }

  async function toggleSub2ApiChannel(channel: Channel) {
    if (siteId == null || !currentAdminSite?.capabilities?.toggle_channel) return;
    const nextStatus =
      normalizedChannelStatus(channel) === "active" ? "disabled" : "active";
    const label = channel.name || `#${channel.id}`;
    setChannelUpdating(channel.id, true);
    setActionError("");
    try {
      const response = await api.updateChannel(siteId, channel.id, {
        status: nextStatus,
      });
      if (!response.success) throw new Error(response.message || "状态更新失败");
      await load(keyword);
      toast.success(`渠道「${label}」已${nextStatus === "active" ? "启用" : "停用"}`);
    } catch (err) {
      const message = errorText(err, "状态更新失败");
      setActionError(`渠道「${label}」状态更新失败：${message}`);
      toast.error(`渠道「${label}」状态更新失败：${message}`);
    } finally {
      setChannelUpdating(channel.id, false);
    }
  }

  async function submitSub2ApiChannel(patch: Partial<Channel>) {
    if (siteId == null || !sub2ApiChannel) return;
    const channel = sub2ApiChannel;
    const label = channel.name || `#${channel.id}`;
    setChannelUpdating(channel.id, true);
    setActionError("");
    try {
      const response = await api.updateChannel(siteId, channel.id, patch);
      if (!response.success) throw new Error(response.message || "渠道配置保存失败");
      await load(keyword);
      toast.success(`渠道「${label}」配置已保存`);
    } catch (err) {
      const message = errorText(err, "渠道配置保存失败");
      setActionError(`渠道「${label}」保存失败：${message}`);
      toast.error(`渠道「${label}」保存失败：${message}`);
      throw new Error(message);
    } finally {
      setChannelUpdating(channel.id, false);
    }
  }

  if (!adminLoading && !adminSites.length) {
    return (
      <>
        <Panel title="主站监控" subtitle="需要先添加 NewAPI 或 sub2api 主站连接">
          {error ? (
            <div className="mb-3 rounded-[var(--radius-sm)] bg-danger-bg px-3 py-2 text-sm text-danger-fg">
              {error}
            </div>
          ) : null}
          <div className="flex flex-col gap-4 py-8 text-center">
            <p className="text-sm text-ink-muted">
              添加你的 NewAPI 或 sub2api 主站后，这里实时读取已有渠道。
              <br />
              NewAPI 可调整优先级；sub2api 可编辑渠道配置并启停渠道。
            </p>
            <Button
              onClick={() => {
                setEditingAdmin(null);
                setAdminFormOpen(true);
              }}
            >
              添加主站
            </Button>
          </div>
        </Panel>
        <AdminSiteFormDialog
          open={adminFormOpen}
          site={editingAdmin}
          onClose={() => setAdminFormOpen(false)}
          onSaved={loadAdminSites}
        />
      </>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="主站监控"
        subtitle="统一读取 NewAPI / sub2api 主站渠道，并按平台能力提供安全的管理操作。"
      />

      {actionError ? (
        <div className="sticky top-[68px] z-30 flex items-start justify-between gap-3 rounded-[var(--radius-sm)] border border-[var(--color-danger-fg)]/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg shadow-[var(--shadow-floating)]">
          <span>{actionError}</span>
          <button
            className="shrink-0 text-[12.5px] opacity-70 hover:opacity-100"
            onClick={() => setActionError("")}
          >
            关闭
          </button>
        </div>
      ) : null}

      <Panel
        title="主站渠道"
        subtitle={
          isSub2Api
            ? "实时读取 sub2api 渠道与分组配置；支持完整编辑和启停，不读取号池"
            : "实时读取 NewAPI /api/channel；本页面仅允许调整优先级"
        }
        action={
          <div className="flex flex-wrap items-center gap-2">
            <Select
              className="w-full sm:w-auto sm:min-w-[200px]"
              value={siteId ?? ""}
              onChange={(event) => setSiteId(Number(event.target.value))}
            >
              {adminSites.map((site) => (
                <option key={site.id} value={site.id}>
                  {site.platform_label || (site.platform === "sub2api" ? "sub2api" : "NewAPI")} · {site.name} · {site.base_url}
                  {site.platform === "sub2api"
                    ? site.has_login_password
                      ? ""
                      : " · 未配置管理员密码"
                    : site.has_access_token
                      ? ""
                      : " · 未配置令牌"}
                </option>
              ))}
            </Select>
            <label className="flex shrink-0 items-center gap-1.5 text-[12.5px] font-medium text-ink-muted">
              <span>消失渠道</span>
              <Select
                className="w-[4.5rem]"
                value={reconcileMode}
                onChange={(event) =>
                  onReconcileModeChange(
                    event.target.value as ReconcileMode,
                  )
                }
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
              onClick={() => void syncCurrentMainSite()}
              disabled={siteId == null}
              loading={syncing}
            >
              {!syncing ? <Cloud size={13} /> : null}
              同步主站
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setEditingAdmin(currentAdminSite);
                setAdminFormOpen(true);
              }}
              disabled={!currentAdminSite}
            >
              编辑主站
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setEditingAdmin(null);
                setAdminFormOpen(true);
              }}
            >
              添加主站
            </Button>
            <span className="mx-1 h-5 w-px bg-line-strong" aria-hidden />
            <Input
              className="w-full sm:w-auto sm:min-w-[170px]"
              placeholder={isSub2Api ? "搜索渠道名/模型" : "搜索渠道名/密钥/模型"}
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void load(keyword);
              }}
            />
            <Button variant="secondary" onClick={() => void load(keyword)}>
              搜索
            </Button>
          </div>
        }
      >
        {error ? (
          <div className="mb-3 rounded-[var(--radius-sm)] bg-danger-bg px-3 py-2 text-sm text-danger-fg">
            {error}
          </div>
        ) : null}

        {staleDataWarning ? (
          <div className="mb-3 rounded-[var(--radius-sm)] border border-[var(--color-warning-fg)]/25 bg-warning-bg px-3 py-2 text-sm text-warning-fg">
            {staleDataWarning}，数据可能已过期。
          </div>
        ) : null}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[280px_minmax(0,1fr)]">
          <aside className="space-y-2">
            <div className="flex items-center justify-between px-1">
              <span className="text-[12.5px] font-bold text-ink-muted">
                分组视角
              </span>
              {groupFilter ? (
                <button
                  className="text-[11px] text-accent"
                  onClick={() => setGroupFilter(null)}
                >
                  清除
                </button>
              ) : null}
            </div>
            <button
              className={`w-full rounded-[var(--radius-sm)] border px-3 py-2 text-left text-sm transition ${
                groupFilter === null
                  ? "border-[var(--color-accent)] bg-sunken"
                  : "border-line hover:bg-sunken-hover"
              }`}
              onClick={() => setGroupFilter(null)}
            >
              <span className="font-semibold text-ink-strong">
                全部渠道
              </span>
              <span className="float-right tabular-nums text-ink-muted">
                {channels.length}
              </span>
            </button>
            {groupRows.map((name) => {
              const info = groups[name];
              return (
                <button
                  key={name}
                  className={`w-full rounded-[var(--radius-sm)] border px-3 py-2 text-left transition ${
                    groupFilter === name
                      ? "border-[var(--color-accent)] bg-sunken"
                      : "border-line hover:bg-sunken-hover"
                  }`}
                  onClick={() =>
                    setGroupFilter(groupFilter === name ? null : name)
                  }
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-semibold text-ink-strong">
                      {name}
                    </span>
                    <span className="tabular-nums text-[12.5px] text-ink-muted">
                      {groupChannelCount[name] || 0} 渠道
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-2">
                    <Badge tone={info ? "info" : "neutral"}>
                      倍率 {ratioText(info)}
                    </Badge>
                    <Badge tone={info ? "success" : "warning"} dot>
                      {info ? "上游已配置" : "仅渠道引用"}
                    </Badge>
                  </div>
                  {info?.desc ? (
                    <div className="mt-1 truncate text-[11px] text-ink-soft">
                      {info.desc}
                    </div>
                  ) : null}
                </button>
              );
            })}
          </aside>

          <div className="min-w-0">
            {loading ? (
              <div className="py-16 text-center text-sm text-ink-muted">
                加载中...
              </div>
            ) : !visibleChannels.length ? (
              <div className="py-16 text-center text-sm text-ink-muted">
                {groupFilter
                  ? `分组「${groupFilter}」下暂无渠道`
                  : "主站当前没有可显示的渠道"}
              </div>
            ) : isSub2Api ? (
              <Sub2ApiChannelTable
                channels={visibleChannels}
                busyChannelIds={updatingChannelIds}
                canEdit={Boolean(currentAdminSite?.capabilities?.edit_channel)}
                canToggle={Boolean(currentAdminSite?.capabilities?.toggle_channel)}
                onEdit={setSub2ApiChannel}
                onToggle={(channel) => void toggleSub2ApiChannel(channel)}
                onRefresh={(channel) => void refreshSub2ApiChannel(channel)}
              />
            ) : (
              <div className="priceai-scrollbar max-h-[calc(100vh-18rem)] overflow-auto rounded-[var(--radius-sm)]">
                <table className="w-full min-w-[1040px] table-fixed text-left text-sm">
                  <colgroup>
                    <col className="w-[170px]" />
                    <col className="w-[112px]" />
                    <col className="w-[190px]" />
                    <col className="w-[72px]" />
                    <col className="w-[72px]" />
                    <col className="w-[92px]" />
                    <col className="w-[270px]" />
                  </colgroup>
                  <thead className="sticky top-0 z-10 bg-panel">
                    <tr className="border-b border-line-soft text-[12.5px] font-semibold text-ink-muted">
                      <th className="pb-2">渠道</th>
                      <th className="pb-2">分组</th>
                      <th className="pb-2">当前 key 上游倍率</th>
                      <th className="pb-2">权重</th>
                      <th className="pb-2">优先级</th>
                      <th className="pb-2">状态</th>
                      <th className="pb-2">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleChannels.map((channel) => {
                      const meta = statusMeta(channel.status);
                      const binding = upstreamBindings[String(channel.id)];
                      const matchedGroups = binding?.matched_groups || [];
                      const staleMatch = isStaleMatchStatus(
                        binding?.match_status,
                      );
                      const partialMatch = binding?.match_status === "matched_partial";
                      const bindingError = bindingFailure(binding);
                      const note = rowNote[channel.id];
                      const displayedGroups = channelGroupNames(
                        channel,
                        allowedGroupNames,
                      );
                      const isMatching = matching.has(channel.id);
                      const isRefreshingKey = refreshingKeyIds.has(channel.id);
                      const isSelected = selectedChannelId === channel.id;
                      return (
                        <tr
                          key={channel.id}
                          className={`border-b border-line-soft last:border-0 hover:bg-sunken-hover ${
                            isSelected ? "bg-sunken" : ""
                          }`}
                        >
                          <td className="max-w-0 py-3 pr-3">
                            <button
                              className="max-w-full text-left"
                              onClick={() =>
                                setSelectedChannelId(isSelected ? null : channel.id)
                              }
                            >
                              <div className="truncate font-bold text-ink-strong">
                                {channel.name || `#${channel.id}`}
                              </div>
                              <div className="truncate text-[11px] text-ink-soft">
                                #{channel.id} · 类型 {channel.type ?? "—"}
                                {channel.models
                                  ? ` · ${splitGroups(channel.models).length} 模型`
                                  : ""}
                              </div>
                            </button>
                            {isMatching ? (
                              <div className="mt-1 flex items-center gap-1.5 text-[11px] font-semibold text-accent">
                                <Spinner />
                                匹配中...
                              </div>
                            ) : note ? (
                              <div
                                className={`mt-1 truncate text-[11px] ${
                                  note.ok
                                    ? "text-ink-muted"
                                    : "text-danger-fg"
                                }`}
                                title={note.text}
                              >
                                {note.text}
                              </div>
                            ) : null}
                          </td>
                          <td className="max-w-0 py-3 pr-3">
                            <div className="flex flex-wrap gap-1">
                              {displayedGroups.map((group) => (
                                <Badge key={group} tone={groups[group] ? "info" : "neutral"}>
                                  {group}
                                </Badge>
                              ))}
                              {!displayedGroups.length ? (
                                <span className="text-[12.5px] text-ink-soft">—</span>
                              ) : null}
                            </div>
                          </td>
                          <td className="max-w-0 py-3 pr-3">
                            {matchedGroups.length ? (
                              <div className="flex max-w-full flex-wrap gap-1 overflow-hidden">
                                {matchedGroups.map((item) => (
                                  <Badge
                                    key={item.name}
                                    tone={
                                      item.available_to_login === false
                                        ? "warning"
                                        : "success"
                                    }
                                    className="max-w-full truncate"
                                  >
                                    {item.name} {bindingRatioText(item.ratio)}
                                  </Badge>
                                ))}
                              </div>
                            ) : (
                              <Badge
                                tone={bindingTone(binding?.match_status)}
                                className="max-w-full truncate"
                                title={bindingTooltip(binding)}
                              >
                                {bindingStatusLabel(binding)}
                              </Badge>
                            )}
                            {staleMatch ? (
                              <div
                                className="mt-1 truncate text-[10px] text-warning-fg"
                                title={bindingError.raw}
                              >
                                {matchedGroups.length
                                  ? `${staleMatchPrefix(binding)} · `
                                  : null}
                                错误原因：{bindingError.summary}
                              </div>
                            ) : partialMatch ? (
                              <div
                                className="mt-1 truncate text-[10px] text-warning-fg"
                                title={binding?.match_message || "部分分组数据不完整"}
                              >
                                部分匹配：{binding?.match_message || "部分分组数据不完整"}
                              </div>
                            ) : binding?.matched_groups?.length ? (
                              <div
                                className="mt-1 truncate text-[10px] text-ink-soft"
                                title={currentKeySummary(binding)}
                              >
                                {currentKeySummary(binding)}
                              </div>
                            ) : null}
                          </td>
                          <td className="py-3 pr-3 tabular-nums text-ink">
                            {Number(channel.weight ?? 0)}
                          </td>
                          <td className="py-3 pr-3 tabular-nums font-semibold text-ink-strong">
                            {Number(channel.priority ?? 0)}
                          </td>
                          <td className="py-3 pr-3">
                            <Badge tone={meta.tone} dot>
                              {meta.label}
                            </Badge>
                          </td>
                          <td className="py-3">
                            <div className="flex flex-nowrap items-center gap-1.5">
                              <Button
                                variant="secondary"
                                size="sm"
                                className="whitespace-nowrap"
                                onClick={() => void matchUpstream(channel)}
                                loading={isMatching}
                              >
                                {!isMatching ? <RefreshCw size={13} /> : null}
                                刷新倍率
                              </Button>
                              <Button
                                variant="secondary"
                                size="sm"
                                className="whitespace-nowrap"
                                onClick={() => void refreshChannelKey(channel)}
                                loading={isRefreshingKey}
                                disabled={refreshingKeyIds.size > 0 && !isRefreshingKey}
                                title="强制读取当前渠道真实 key，并重新匹配倍率"
                              >
                                {!isRefreshingKey ? <KeyRound size={13} /> : null}
                                刷新 key
                              </Button>
                              <Button
                                variant="secondary"
                                size="sm"
                                className="whitespace-nowrap"
                                onClick={() => setPriorityChannel(channel)}
                              >
                                编辑优先级
                              </Button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </Panel>

      {!isSub2Api && selectedChannel ? (
          <Panel
            title={`倍率详情 · ${selectedChannel.name || `#${selectedChannel.id}`}`}
            subtitle={
              selectedBindingStale
                ? "以下为上次成功匹配的分组与倍率"
                : "当前渠道 key 匹配到的上游分组与倍率"
            }
          action={
            <button
              className="text-[12.5px] text-ink-muted"
              onClick={() => setSelectedChannelId(null)}
            >
              收起
            </button>
          }
          >
            {selectedBindingStale ? (
              <div
                className="mb-3 rounded-[var(--radius-sm)] bg-warning-bg px-3 py-2 text-sm text-warning-fg"
                title={selectedBindingError.raw}
              >
                {staleMatchPrefix(selectedBinding)} · 错误原因：
                {selectedBindingError.summary}
              </div>
            ) : null}
            <div className="priceai-scrollbar overflow-x-auto pb-1">
            <table className="w-full min-w-max table-auto text-left text-sm">
              <thead>
                <tr className="border-b border-line-soft text-[12.5px] font-semibold text-ink-muted">
                  <th className="pb-2">分组</th>
                  <th className="pb-2">上游倍率</th>
                  <th className="pb-2">分组状态</th>
                  <th className="pb-2">描述</th>
                </tr>
              </thead>
              <tbody>
                {(selectedBinding?.matched_groups || []).map((item) => (
                  <tr
                    key={item.name}
                    className="border-b border-line-soft last:border-0"
                  >
                    <td className="py-2 pr-3 font-semibold text-ink-strong">
                      {item.name}
                    </td>
                    <td className="py-2 pr-3 tabular-nums">
                      {bindingRatioText(item.ratio)}
                    </td>
                    <td className="py-2 pr-3">
                          <Badge
                            tone={
                              selectedBindingStale || item.available_to_login === false
                                ? "warning"
                                : "success"
                            }
                            dot
                          >
                            {selectedBindingStale
                              ? "上次成功数据"
                              : item.available_to_login === false
                              ? "上游未找到该分组"
                              : "上游已配置"}
                      </Badge>
                    </td>
                    <td className="py-2 pr-3 text-ink-muted">
                      {item.desc || "—"}
                    </td>
                  </tr>
                ))}
                {!(selectedBinding?.matched_groups || []).length ? (
                  <tr>
                    <td
                      colSpan={4}
                      className="py-3 text-center text-ink-soft"
                    >
                      {isStaleMatchStatus(selectedBinding?.match_status)
                        ? `错误原因：${bindingFailure(selectedBinding).summary}`
                        : selectedBinding?.match_message || "尚未取得上游分组倍率"}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}

      <ChannelPriorityDialog
        open={priorityChannel !== null}
        channel={priorityChannel}
        onClose={() => setPriorityChannel(null)}
        onSubmit={(priority) => submitForm(priorityChannel!, priority)}
      />

      <Sub2ApiChannelDialog
        open={sub2ApiChannel !== null}
        channel={sub2ApiChannel}
        groups={sub2ApiGroups}
        onClose={() => setSub2ApiChannel(null)}
        onSubmit={submitSub2ApiChannel}
      />

      <AdminSiteFormDialog
        open={adminFormOpen}
        site={editingAdmin}
        onClose={() => setAdminFormOpen(false)}
        onSaved={loadAdminSites}
        onVerified={async () => {
          await load("");
        }}
      />
    </div>
  );
}
