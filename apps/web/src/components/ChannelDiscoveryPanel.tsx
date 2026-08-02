import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Check,
  ExternalLink,
  Link2,
  RefreshCw,
  Search,
} from "lucide-react";
import { syncSiteBrowserSession } from "@/lib/browserSessionBridge";
import { api } from "@/lib/api";
import { errorText, useToast } from "@/components/Toast";
import type {
  AdminSite,
  ChannelDiscoveryCandidate,
  ChannelDiscoveryImportResult,
} from "@/lib/types";
import { Badge } from "./Badge";
import { Button, Input, Select, Spinner } from "./ui";

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
  return {
    ready: "登录态已同步",
    syncing: "同步中",
    pending: "等待扩展",
    validating: "验证中",
    no_session: "没有登录态",
    expired: "登录态已失效",
    permission_required: "需要站点权限",
    extension_unavailable: "扩展未连接",
    failed: "同步失败",
    existing: "已监控",
    created: "已创建，待同步",
  }[status] || status || "待处理";
}

function sessionTone(
  status: string,
): "neutral" | "success" | "warning" | "danger" | "info" {
  if (status === "ready" || status === "existing") return "success";
  if (status === "syncing" || status === "created") return "info";
  if (
    status === "no_session" ||
    status === "expired" ||
    status === "permission_required"
  )
    return "warning";
  if (status === "failed" || status === "extension_unavailable") return "danger";
  return "neutral";
}

function initialRowState(candidate: ChannelDiscoveryCandidate): RowState {
  if (candidate.existing_site_id) {
    const authMode = candidate.existing_site_auth_mode || null;
    const syncStatus = candidate.existing_site_session_sync_status || "";
    const browserStatus =
      authMode === "browser" && syncStatus && syncStatus !== "not_requested"
        ? syncStatus
        : "existing";
    return {
      status:
        browserStatus === "existing" && candidate.existing_site_status === "ready"
          ? "ready"
          : browserStatus,
      siteId: candidate.existing_site_id,
      authMode,
      canSync: authMode === "browser",
    };
  }
  return { status: "" };
}

export function ChannelDiscoveryPanel({
  open,
  onClose,
  onImported,
  onEditSite,
}: {
  open: boolean;
  onClose: () => void;
  onImported: () => Promise<void> | void;
  onEditSite: (siteId: number) => void;
}) {
  const toast = useToast();
  const [adminSites, setAdminSites] = useState<AdminSite[]>([]);
  const [adminSiteId, setAdminSiteId] = useState<number | null>(null);
  const [candidates, setCandidates] = useState<ChannelDiscoveryCandidate[]>([]);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [rowStates, setRowStates] = useState<Record<string, RowState>>({});
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingSites, setLoadingSites] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [intervalMinutes, setIntervalMinutes] = useState(3);
  const [progress, setProgress] = useState<{
    current: number;
    total: number;
    baseUrl: string;
  } | null>(null);

  const loadAdminSites = useCallback(async () => {
    setLoadingSites(true);
    try {
      const response = await api.adminSites();
      const newApiSites = (response.data || []).filter(
        (site) => site.platform === "newapi",
      );
      setAdminSites(newApiSites);
      setAdminSiteId((previous) =>
        previous && newApiSites.some((site) => site.id === previous)
          ? previous
          : newApiSites[0]?.id ?? null,
      );
      setMessage(newApiSites.length ? "" : "暂无可用的 NewAPI 主站");
    } catch (err) {
      setAdminSites([]);
      setAdminSiteId(null);
      setMessage(errorText(err, "主站列表加载失败"));
    } finally {
      setLoadingSites(false);
    }
  }, []);

  const loadCandidates = useCallback(async () => {
    if (adminSiteId == null) return;
    setLoading(true);
    setMessage("");
    try {
      const response = await api.channelCandidates(adminSiteId);
      const next = response.data || [];
      setCandidates(next);
      setSelected((previous) => {
        const valid = new Set(next.map(candidateKey));
        return new Set([...previous].filter((key) => valid.has(key)));
      });
      setRowStates((previous) => {
        const nextStates: Record<string, RowState> = {};
        for (const candidate of next) {
          nextStates[candidateKey(candidate)] =
            previous[candidateKey(candidate)] || initialRowState(candidate);
        }
        return nextStates;
      });
    } catch (err) {
      setCandidates([]);
      setSelected(new Set());
      setMessage(errorText(err, "候选渠道加载失败"));
    } finally {
      setLoading(false);
    }
  }, [adminSiteId]);

  useEffect(() => {
    if (open) void loadAdminSites();
  }, [open, loadAdminSites]);

  useEffect(() => {
    if (open && adminSiteId != null) void loadCandidates();
  }, [open, adminSiteId, loadCandidates]);

  const filteredCandidates = useMemo(() => {
    const query = keyword.trim().toLowerCase();
    if (!query) return candidates;
    return candidates.filter((candidate) =>
      `${candidate.name} ${candidate.base_url} ${candidate.channel_names.join(" ")}`
        .toLowerCase()
        .includes(query),
    );
  }, [candidates, keyword]);

  const selectedCandidates = useMemo(
    () => candidates.filter((candidate) => selected.has(candidateKey(candidate))),
    [candidates, selected],
  );

  const stats = useMemo(() => {
    const existing = candidates.filter((candidate) => candidate.existing_site_id).length;
    const pending = candidates.length - existing;
    const waiting = candidates.filter((candidate) => {
      const status = rowStates[candidateKey(candidate)]?.status;
      return [
        "no_session",
        "expired",
        "permission_required",
        "extension_unavailable",
        "failed",
      ].includes(status || "");
    }).length;
    return { total: candidates.length, existing, pending, waiting };
  }, [candidates, rowStates]);

  function toggleCandidate(candidate: ChannelDiscoveryCandidate) {
    const key = candidateKey(candidate);
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleAllVisible() {
    setSelected((previous) => {
      const next = new Set(previous);
      const allSelected = filteredCandidates.every((candidate) =>
        next.has(candidateKey(candidate)),
      );
      for (const candidate of filteredCandidates) {
        const key = candidateKey(candidate);
        if (allSelected) next.delete(key);
        else next.add(key);
      }
      return next;
    });
  }

  function setRowState(key: string, state: RowState) {
    setRowStates((previous) => ({ ...previous, [key]: state }));
  }

  async function syncImportedRow(
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
    const existingBrowserSite =
      result.status === "existing" &&
      candidate.existing_site_auth_mode === "browser" &&
      candidate.existing_site_session_sync_status !== "ready";
    if (result.status === "existing" && !existingBrowserSite) {
      setRowState(key, {
        status:
          candidate.existing_site_auth_mode === "browser" &&
          candidate.existing_site_session_sync_status === "ready"
            ? "ready"
            : "existing",
        siteId: result.site_id,
        authMode: candidate.existing_site_auth_mode || null,
        canSync: candidate.existing_site_auth_mode === "browser",
      });
      return;
    }
    setRowState(key, {
      status: "syncing",
      siteId: result.site_id,
      authMode: candidate.existing_site_auth_mode || "browser",
      canSync: true,
    });
    try {
      const syncResult = await syncSiteBrowserSession(result.site_id);
      setRowState(key, {
        status: syncResult.status,
        siteId: result.site_id,
        authMode: candidate.existing_site_auth_mode || "browser",
        canSync: true,
        message: syncResult.message || syncResult.error_code || undefined,
      });
    } catch (err) {
      setRowState(key, {
        status: "failed",
        siteId: result.site_id,
        authMode: candidate.existing_site_auth_mode || "browser",
        canSync: true,
        message: errorText(err, "登录态同步失败"),
      });
    }
  }

  async function importSelected() {
    if (adminSiteId == null || !selectedCandidates.length || busy) return;
    setBusy(true);
    setMessage("");
    try {
      const response = await api.importDiscoveredSites({
        admin_site_id: adminSiteId,
        interval_minutes: intervalMinutes,
        items: selectedCandidates.map((candidate) => ({
          base_url: candidate.base_url,
          name: candidate.name,
          channel_ids: candidate.channel_ids,
          channel_names: candidate.channel_names,
        })),
      });
      const results = response.data || [];
      const resultByUrl = new Map(results.map((result) => [result.base_url, result]));
      for (const [index, candidate] of selectedCandidates.entries()) {
        setProgress({
          current: index + 1,
          total: selectedCandidates.length,
          baseUrl: candidate.base_url,
        });
        const result = resultByUrl.get(candidate.base_url);
        if (!result) {
          setRowState(candidateKey(candidate), {
            status: "failed",
            authMode: candidate.existing_site_auth_mode || "browser",
            canSync: candidate.existing_site_auth_mode === "browser" || !candidate.existing_site_id,
            message: "后端未返回该候选结果",
          });
          continue;
        }
        await syncImportedRow(candidate, result);
      }
      setSelected(new Set());
      await onImported();
      const failed = results.filter((result) =>
        ["invalid", "conflict", "failed"].includes(result.status),
      ).length;
      if (failed) {
        toast.info(`已处理 ${results.length - failed} 个候选，${failed} 个需要处理`);
      } else {
        toast.success(`已处理 ${results.length} 个候选渠道`);
      }
    } catch (err) {
      const text = errorText(err, "批量导入失败");
      setMessage(text);
      toast.error(text);
    } finally {
      setProgress(null);
      setBusy(false);
    }
  }

  async function retry(candidate: ChannelDiscoveryCandidate) {
    const state = rowStates[candidateKey(candidate)];
    if (!state?.siteId || busy) return;
    setRowState(candidateKey(candidate), { ...state, status: "syncing" });
    try {
      const result = await syncSiteBrowserSession(state.siteId);
      setRowState(candidateKey(candidate), {
        status: result.status,
        siteId: state.siteId,
        authMode: state.authMode,
        canSync: state.canSync,
        message: result.message || result.error_code || undefined,
      });
      await onImported();
    } catch (err) {
      setRowState(candidateKey(candidate), {
        ...state,
        status: "failed",
        message: errorText(err, "登录态同步失败"),
      });
    }
  }

  function openLogin(candidate: ChannelDiscoveryCandidate) {
    window.open(candidate.base_url, "_blank", "noopener,noreferrer");
  }

  if (!open) return null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Link2 size={15} className="shrink-0 text-[var(--color-brand)]" aria-hidden />
          <span className="text-sm font-semibold text-[var(--color-text-primary)]">
            从主站发现 · NewAPI
          </span>
          <Badge tone="info">只读发现</Badge>
        </div>
        <Button
          variant="secondary"
          size="sm"
          className="h-8"
          onClick={() => void loadCandidates()}
          loading={loading || loadingSites}
          disabled={adminSiteId == null}
          aria-label="刷新候选渠道"
        >
          {!loading && !loadingSites ? <RefreshCw size={13} /> : null}
          刷新
        </Button>
      </div>

      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <label className="block space-y-1.5">
          <span className="text-xs font-semibold text-[var(--color-text-muted)]">来源主站</span>
          <Select
            value={adminSiteId ?? ""}
            onChange={(event) => setAdminSiteId(Number(event.target.value) || null)}
            disabled={loadingSites || !adminSites.length}
            aria-label="来源主站"
          >
            {!adminSites.length ? <option value="">暂无 NewAPI 主站</option> : null}
            {adminSites.map((site) => (
              <option key={site.id} value={site.id}>
                {site.name} · {site.base_url}
              </option>
            ))}
          </Select>
        </label>
        <label className="block space-y-1.5">
          <span className="text-xs font-semibold text-[var(--color-text-muted)]">筛选候选</span>
          <div className="relative">
            <Search
              size={14}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-soft)]"
              aria-hidden
            />
            <Input
              className="pl-9"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="名称或 Base URL"
              aria-label="筛选候选"
            />
          </div>
        </label>
        <label className="block space-y-1.5 sm:col-span-2">
          <span className="text-xs font-semibold text-[var(--color-text-muted)]">
            新建渠道监控间隔（分钟）
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="w-32"
              type="number"
              min={1}
              max={1440}
              value={intervalMinutes}
              onChange={(event) => {
                const parsed = Number(event.target.value);
                if (!Number.isFinite(parsed)) return;
                setIntervalMinutes(Math.min(1440, Math.max(1, Math.trunc(parsed))));
              }}
              aria-label="新建渠道监控间隔"
            />
            <span className="text-[11px] text-[var(--color-text-soft)]">
              新建站点使用此间隔，已存在站点保留原配置
            </span>
          </div>
        </label>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ["发现", stats.total],
          ["已监控", stats.existing],
          ["待添加", stats.pending],
          ["待处理", stats.waiting],
        ].map(([label, value]) => (
          <div
            key={String(label)}
            className="rounded-xl border border-[var(--color-border)] bg-[var(--color-panel-soft)] px-3 py-2"
          >
            <div className="text-[11px] text-[var(--color-text-muted)]">{label}</div>
            <div className="mt-0.5 text-xl font-extrabold tabular-nums text-[var(--color-text-primary)]">
              {value}
            </div>
          </div>
        ))}
      </div>

      {message ? (
        <div className="rounded-lg border border-[var(--color-warning-text)]/25 bg-[var(--color-warning-bg)] px-3 py-2 text-xs text-[var(--color-warning-text)]">
          {message}
        </div>
      ) : null}

      {progress ? (
        <div className="flex min-w-0 items-center gap-3 rounded-lg border border-[var(--color-brand)]/25 bg-[var(--color-success-bg)] px-3 py-2 text-xs text-[var(--color-success-text)]">
          <Spinner />
          <span className="min-w-0 truncate">
            正在同步登录态 {progress.current}/{progress.total} · {progress.baseUrl}
          </span>
        </div>
      ) : null}

      <div className="priceai-scrollbar hidden min-w-0 overflow-x-auto rounded-xl border border-[var(--color-border-subtle)] sm:block">
        <table className="w-full min-w-[760px] table-fixed text-left text-sm">
          <colgroup>
            <col className="w-10" />
            <col className="w-[22%]" />
            <col className="w-[34%]" />
            <col className="w-[18%]" />
            <col className="w-[20%]" />
          </colgroup>
          <thead className="bg-[var(--color-panel-soft)] text-xs font-semibold text-[var(--color-text-muted)]">
            <tr className="border-b border-[var(--color-border-subtle)]">
              <th className="px-3 py-2.5">
                <input
                  type="checkbox"
                  checked={
                    filteredCandidates.length > 0 &&
                    filteredCandidates.every((candidate) => selected.has(candidateKey(candidate)))
                  }
                  onChange={toggleAllVisible}
                  aria-label="全选候选渠道"
                />
              </th>
              <th className="px-3 py-2.5">来源渠道</th>
              <th className="px-3 py-2.5">Base URL</th>
              <th className="px-3 py-2.5">监控状态</th>
              <th className="px-3 py-2.5">操作</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={5} className="px-3 py-12 text-center text-sm text-[var(--color-text-muted)]">
                  <span className="inline-flex items-center gap-2"><Spinner /> 正在读取主站渠道</span>
                </td>
              </tr>
            ) : !filteredCandidates.length ? (
              <tr>
                <td colSpan={5} className="px-3 py-12 text-center text-sm text-[var(--color-text-muted)]">
                  暂无匹配候选
                </td>
              </tr>
            ) : (
              filteredCandidates.map((candidate) => {
                const key = candidateKey(candidate);
                const state = rowStates[key] || initialRowState(candidate);
                const retryable = [
                  "no_session",
                  "expired",
                  "permission_required",
                  "failed",
                  "extension_unavailable",
                ].includes(state.status);
                const canRetryExisting =
                  Boolean(state.siteId && state.canSync) &&
                  (state.status === "existing" || retryable);
                return (
                  <tr
                    key={key}
                    className="border-b border-[var(--color-border-subtle)] last:border-0 hover:bg-[var(--color-surface-hover)]"
                  >
                    <td className="px-3 py-3 align-top">
                      <input
                        type="checkbox"
                        checked={selected.has(key)}
                        onChange={() => toggleCandidate(candidate)}
                        aria-label={`选择 ${candidate.name}`}
                      />
                    </td>
                    <td className="max-w-0 px-3 py-3 align-top">
                      <div className="truncate font-semibold text-[var(--color-text-primary)]">
                        {candidate.name}
                      </div>
                      <div className="mt-1 truncate text-[11px] text-[var(--color-text-soft)]" title={candidate.channel_names.join("、")}>
                        {candidate.channel_count} 个主站渠道
                      </div>
                    </td>
                    <td className="max-w-0 px-3 py-3 align-top">
                      <code className="block truncate text-xs text-[var(--color-text-body)]" title={candidate.base_url}>
                        {candidate.base_url}
                      </code>
                    </td>
                    <td className="px-3 py-3 align-top">
                      <Badge tone={sessionTone(state.status)} dot>
                        {state.status ? sessionLabel(state.status) : candidate.existing_site_id ? "已监控" : "待添加"}
                      </Badge>
                      {state.message ? (
                        <div className="mt-1 max-w-[180px] truncate text-[10px] text-[var(--color-danger-text)]" title={state.message}>
                          {state.message}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-3 py-3 align-top">
                      <div className="flex flex-wrap gap-1.5">
                        {state.siteId ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8"
                            onClick={() => onEditSite(Number(state.siteId))}
                            aria-label="编辑认证"
                          >
                            编辑认证
                          </Button>
                        ) : null}
                        {canRetryExisting ? (
                          <Button
                            variant="secondary"
                            size="sm"
                            className="h-8"
                            onClick={() => void retry(candidate)}
                            disabled={busy}
                            aria-label="重新同步"
                          >
                            <RefreshCw size={13} />
                            {state.status === "existing" ? "同步登录态" : "重新同步"}
                          </Button>
                        ) : null}
                        {retryable ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8"
                            onClick={() => openLogin(candidate)}
                            aria-label="打开登录页"
                          >
                            <ExternalLink size={13} />
                            打开登录页
                          </Button>
                        ) : null}
                        {state.status === "ready" ? <Check size={16} className="mt-1 text-[var(--color-brand)]" aria-label="已同步" /> : null}
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="space-y-2 sm:hidden">
        {loading ? (
          <div className="rounded-xl border border-[var(--color-border-subtle)] px-3 py-10 text-center text-sm text-[var(--color-text-muted)]">
            <span className="inline-flex items-center gap-2"><Spinner /> 正在读取主站渠道</span>
          </div>
        ) : !filteredCandidates.length ? (
          <div className="rounded-xl border border-[var(--color-border-subtle)] px-3 py-10 text-center text-sm text-[var(--color-text-muted)]">
            暂无匹配候选
          </div>
        ) : (
          filteredCandidates.map((candidate) => {
            const key = candidateKey(candidate);
            const state = rowStates[key] || initialRowState(candidate);
            const retryable = [
              "no_session",
              "expired",
              "permission_required",
              "failed",
              "extension_unavailable",
            ].includes(state.status);
            const canRetryExisting =
              Boolean(state.siteId && state.canSync) &&
              (state.status === "existing" || retryable);
            return (
              <div
                key={`mobile-${key}`}
                className="space-y-2 rounded-xl border border-[var(--color-border-subtle)] bg-[var(--color-panel-soft)] p-3"
              >
                <div className="flex min-w-0 items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-1 shrink-0"
                    checked={selected.has(key)}
                    onChange={() => toggleCandidate(candidate)}
                    aria-label={`选择 ${candidate.name}`}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="break-words font-semibold text-[var(--color-text-primary)]">
                          {candidate.name}
                        </div>
                        <div className="mt-0.5 text-[11px] text-[var(--color-text-soft)]">
                          {candidate.channel_count} 个主站渠道
                        </div>
                      </div>
                      <Badge tone={sessionTone(state.status)} dot>
                        {state.status
                          ? sessionLabel(state.status)
                          : candidate.existing_site_id
                            ? "已监控"
                            : "待添加"}
                      </Badge>
                    </div>
                    <code className="mt-2 block break-all text-[11px] leading-5 text-[var(--color-text-body)]">
                      {candidate.base_url}
                    </code>
                    {state.message ? (
                      <div className="mt-1 break-words text-[10px] text-[var(--color-danger-text)]">
                        {state.message}
                      </div>
                    ) : null}
                  </div>
                </div>
                {canRetryExisting || retryable || state.siteId ? (
                  <div className="flex flex-wrap gap-1.5 pl-6">
                    {state.siteId ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8"
                        onClick={() => onEditSite(Number(state.siteId))}
                        aria-label="编辑认证"
                      >
                        编辑认证
                      </Button>
                    ) : null}
                    {canRetryExisting ? (
                      <Button
                        variant="secondary"
                        size="sm"
                        className="h-8"
                        onClick={() => void retry(candidate)}
                        disabled={busy}
                        aria-label="重新同步"
                      >
                        <RefreshCw size={13} />
                        {state.status === "existing" ? "同步登录态" : "重新同步"}
                      </Button>
                    ) : null}
                    {retryable ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8"
                        onClick={() => openLogin(candidate)}
                        aria-label="打开登录页"
                      >
                        <ExternalLink size={13} />
                        打开登录页
                      </Button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--color-border-subtle)] pt-3">
        <span className="text-xs text-[var(--color-text-muted)]">
          已选择 <b className="tabular-nums text-[var(--color-text-primary)]">{selectedCandidates.length}</b> 个候选
        </span>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" className="h-8" onClick={onClose} disabled={busy}>
            返回手动添加
          </Button>
          <Button
            variant="brand"
            size="sm"
            className="h-8"
            onClick={() => void importSelected()}
            loading={busy}
            disabled={!selectedCandidates.length || adminSiteId == null}
          >
            {!busy ? <Link2 size={13} /> : null}
            添加并同步
          </Button>
        </div>
      </div>
    </div>
  );
}
