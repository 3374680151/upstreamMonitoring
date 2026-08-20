import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Check,
  ExternalLink,
  Link2,
  RefreshCw,
  Search,
} from "lucide-react";
import { api } from "@/lib/api";
import {
  isSessionSyncRetryable,
  syncSiteBrowserSession,
} from "@/lib/browserSessionBridge";
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
    existing: "已监控",
    created: "已创建，待同步登录",
    not_requested: "待同步登录",
    pending: "正在同步登录",
    validating: "正在验证登录",
    ready: "登录态已同步",
    no_session: "未检测到登录",
    expired: "登录态已过期",
    permission_required: "需要浏览器权限",
    extension_unavailable: "扩展未连接",
    failed: "同步失败",
  }[status] || status || "待处理";
}

function sessionTone(
  status: string,
): "neutral" | "success" | "warning" | "danger" | "info" {
  if (status === "existing") return "success";
  if (status === "ready") return "success";
  if (status === "created" || status === "pending" || status === "validating") return "info";
  if (["no_session", "expired", "permission_required"].includes(status)) {
    return "warning";
  }
  if (
    ["failed", "extension_unavailable", "conflict", "invalid"].includes(status)
  ) {
    return "danger";
  }
  return "neutral";
}

function initialRowState(candidate: ChannelDiscoveryCandidate): RowState {
  if (candidate.existing_site_id) {
    const authMode = candidate.existing_site_auth_mode || null;
    const syncStatus =
      authMode === "browser"
        ? String(candidate.existing_site_session_sync_status || "not_requested")
        : "existing";
    return {
      status: syncStatus,
      siteId: candidate.existing_site_id,
      authMode,
      canSync:
        authMode === "browser" &&
        candidate.existing_site_enabled !== false &&
        !["ready", "pending", "validating"].includes(syncStatus),
    };
  }
  return { status: "" };
}

function shouldSyncCandidate(
  candidate: ChannelDiscoveryCandidate,
  result: ChannelDiscoveryImportResult,
): boolean {
  // Newly imported rows are created in browser mode by the backend. Existing
  // rows are synced only when their non-sensitive auth mode says browser;
  // token/password rows must retain their existing credentials untouched.
  if (!result.site_id) return false;
  if (result.status === "created") return true;
  return (
    result.status === "existing" &&
    candidate.existing_site_auth_mode === "browser" &&
    candidate.existing_site_enabled !== false
  );
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
    stage?: "import" | "sync";
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
    const existing = candidates.filter(
      (candidate) =>
        candidate.existing_site_id || rowStates[candidateKey(candidate)]?.siteId,
    ).length;
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
    if (result.status !== "created" && result.status !== "existing") {
      setRowState(key, {
        status: result.status || "failed",
        siteId: result.site_id,
        authMode: candidate.existing_site_auth_mode || null,
        canSync: false,
        message: result.message || "导入未完成",
      });
      return;
    }
    setRowState(key, {
      status: result.status === "created" ? "created" : "existing",
      siteId: result.site_id,
      authMode:
        result.status === "created"
          ? "browser"
          : candidate.existing_site_auth_mode || null,
      canSync: false,
      message: result.message || undefined,
    });
  }

  async function syncImportedRow(
    candidate: ChannelDiscoveryCandidate,
    siteId: number,
  ): Promise<void> {
    const key = candidateKey(candidate);
    setRowState(key, {
      status: "pending",
      siteId,
      authMode: "browser",
      canSync: false,
    });
    try {
      const result = await syncSiteBrowserSession(siteId);
      const retryable = isSessionSyncRetryable(result.status);
      setRowState(key, {
        status: result.status,
        siteId,
        authMode: "browser",
        canSync: retryable,
        message:
          result.status === "ready"
            ? undefined
            : result.message || result.error_code || "登录态同步未完成",
      });
    } catch (err) {
      setRowState(key, {
        status: "failed",
        siteId,
        authMode: "browser",
        canSync: true,
        message: errorText(err, "登录态同步失败"),
      });
    }
  }

  async function retryImportedRow(candidate: ChannelDiscoveryCandidate): Promise<void> {
    const state = rowStates[candidateKey(candidate)];
    if (!state?.siteId || !state.canSync || busy) return;
    setBusy(true);
    setProgress({
      current: 1,
      total: 1,
      baseUrl: candidate.base_url,
      stage: "sync",
    });
    try {
      await syncImportedRow(candidate, Number(state.siteId));
      await onImported();
    } catch (err) {
      const message = errorText(err, "登录态同步结果刷新失败");
      setMessage(message);
      toast.error(message);
    } finally {
      setProgress(null);
      setBusy(false);
    }
  }

  function openCandidateLogin(candidate: ChannelDiscoveryCandidate): void {
    const baseUrl = candidate.base_url.trim().replace(/\/+$/, "");
    if (baseUrl) window.open(baseUrl, "_blank", "noopener,noreferrer");
  }

  function canOpenCandidateLogin(status: string): boolean {
    return ["no_session", "expired", "permission_required"].includes(status);
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
          stage: "import",
        });
        const result = resultByUrl.get(candidate.base_url);
        if (!result) {
          setRowState(candidateKey(candidate), {
            status: "failed",
            authMode: candidate.existing_site_auth_mode || null,
            canSync: false,
            message: "后端未返回该候选结果",
          });
          continue;
        }
        applyImportedRow(candidate, result);
        if (shouldSyncCandidate(candidate, result)) {
          setProgress({
            current: index + 1,
            total: selectedCandidates.length,
            baseUrl: candidate.base_url,
            stage: "sync",
          });
          await syncImportedRow(candidate, Number(result.site_id));
        }
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

  if (!open) return null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Link2 size={15} className="shrink-0 text-accent" aria-hidden />
          <span className="text-sm font-semibold text-ink-strong">
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
          <span className="text-[12.5px] font-semibold text-ink-muted">来源主站</span>
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
          <span className="text-[12.5px] font-semibold text-ink-muted">筛选候选</span>
          <div className="relative">
            <Search
              size={14}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-soft"
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
          <span className="text-[12.5px] font-semibold text-ink-muted">
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
            <span className="text-[11px] text-ink-soft">
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
            className="rounded-xl border border-line bg-panel-soft px-3 py-2"
          >
            <div className="text-[11px] text-ink-muted">{label}</div>
            <div className="mt-0.5 text-xl font-extrabold tabular-nums text-ink-strong">
              {value}
            </div>
          </div>
        ))}
      </div>

      {message ? (
        <div className="rounded-[var(--radius-md)] border border-[var(--color-warning-fg)]/25 bg-warning-bg px-3 py-2 text-[12.5px] text-warning-fg">
          {message}
        </div>
      ) : null}

      {progress ? (
        <div className="flex min-w-0 items-center gap-3 rounded-[var(--radius-md)] border border-[var(--color-accent)]/25 bg-success-bg px-3 py-2 text-[12.5px] text-success-fg">
          <Spinner />
          <span className="min-w-0 truncate">
            {progress.stage === "sync" ? "正在同步浏览器登录态" : "正在创建监控渠道"} {progress.current}/{progress.total} · {progress.baseUrl}
          </span>
        </div>
      ) : null}

      <div className="priceai-scrollbar hidden min-w-0 overflow-x-auto rounded-xl border border-line-soft sm:block">
        <table className="w-full min-w-[760px] table-fixed text-left text-sm">
          <colgroup>
            <col className="w-10" />
            <col className="w-[22%]" />
            <col className="w-[34%]" />
            <col className="w-[18%]" />
            <col className="w-[20%]" />
          </colgroup>
          <thead className="bg-panel-soft text-[12.5px] font-semibold text-ink-muted">
            <tr className="border-b border-line-soft">
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
                <td colSpan={5} className="px-3 py-12 text-center text-sm text-ink-muted">
                  <span className="inline-flex items-center gap-2"><Spinner /> 正在读取主站渠道</span>
                </td>
              </tr>
            ) : !filteredCandidates.length ? (
              <tr>
                <td colSpan={5} className="px-3 py-12 text-center text-sm text-ink-muted">
                  暂无匹配候选
                </td>
              </tr>
            ) : (
              filteredCandidates.map((candidate) => {
                const key = candidateKey(candidate);
                const state = rowStates[key] || initialRowState(candidate);
                return (
                  <tr
                    key={key}
                    className="border-b border-line-soft last:border-0 hover:bg-sunken-hover"
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
                      <div className="truncate font-semibold text-ink-strong">
                        {candidate.name}
                      </div>
                      <div className="mt-1 truncate text-[11px] text-ink-soft" title={candidate.channel_names.join("、")}>
                        {candidate.channel_count} 个主站渠道
                      </div>
                    </td>
                    <td className="max-w-0 px-3 py-3 align-top">
                      <code className="block truncate text-[12.5px] text-ink" title={candidate.base_url}>
                        {candidate.base_url}
                      </code>
                    </td>
                    <td className="px-3 py-3 align-top">
                      <Badge tone={sessionTone(state.status)} dot>
                        {state.status ? sessionLabel(state.status) : candidate.existing_site_id ? "已监控" : "待添加"}
                      </Badge>
                      {state.message ? (
                        <div className="mt-1 max-w-[180px] truncate text-[10px] text-danger-fg" title={state.message}>
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
                        {state.siteId && state.authMode === "browser" && state.canSync ? (
                          <Button
                            variant="secondary"
                            size="sm"
                            className="h-8"
                            onClick={() => void retryImportedRow(candidate)}
                            loading={busy}
                            aria-label="重新同步登录态"
                          >
                            {!busy ? <RefreshCw size={12} /> : null}
                            重新同步
                          </Button>
                        ) : null}
                        {state.authMode === "browser" && canOpenCandidateLogin(state.status) ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8"
                            onClick={() => openCandidateLogin(candidate)}
                            disabled={busy}
                            aria-label="打开登录页"
                          >
                            <ExternalLink size={12} />
                            打开登录页
                          </Button>
                        ) : null}
                        {state.siteId ? <Check size={16} className="mt-1 text-accent" aria-label="已创建" /> : null}
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
          <div className="rounded-xl border border-line-soft px-3 py-10 text-center text-sm text-ink-muted">
            <span className="inline-flex items-center gap-2"><Spinner /> 正在读取主站渠道</span>
          </div>
        ) : !filteredCandidates.length ? (
          <div className="rounded-xl border border-line-soft px-3 py-10 text-center text-sm text-ink-muted">
            暂无匹配候选
          </div>
        ) : (
          filteredCandidates.map((candidate) => {
            const key = candidateKey(candidate);
            const state = rowStates[key] || initialRowState(candidate);
            return (
              <div
                key={`mobile-${key}`}
                className="space-y-2 rounded-xl border border-line-soft bg-panel-soft p-3"
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
                        <div className="break-words font-semibold text-ink-strong">
                          {candidate.name}
                        </div>
                        <div className="mt-0.5 text-[11px] text-ink-soft">
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
                    <code className="mt-2 block break-all text-[11px] leading-5 text-ink">
                      {candidate.base_url}
                    </code>
                    {state.message ? (
                      <div className="mt-1 break-words text-[10px] text-danger-fg">
                        {state.message}
                      </div>
                    ) : null}
                  </div>
                </div>
                {state.siteId ? (
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
                    {state.siteId && state.authMode === "browser" && state.canSync ? (
                      <Button
                        variant="secondary"
                        size="sm"
                        className="h-8"
                        onClick={() => void retryImportedRow(candidate)}
                        loading={busy}
                        aria-label="重新同步登录态"
                      >
                        {!busy ? <RefreshCw size={12} /> : null}
                        重新同步
                      </Button>
                    ) : null}
                    {state.authMode === "browser" && canOpenCandidateLogin(state.status) ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8"
                        onClick={() => openCandidateLogin(candidate)}
                        disabled={busy}
                        aria-label="打开登录页"
                      >
                        <ExternalLink size={12} />
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

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line-soft pt-3">
        <span className="text-[12.5px] text-ink-muted">
          已选择 <b className="tabular-nums text-ink-strong">{selectedCandidates.length}</b> 个候选
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
            添加渠道
          </Button>
        </div>
      </div>
    </div>
  );
}
