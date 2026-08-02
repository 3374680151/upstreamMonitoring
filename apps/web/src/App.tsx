import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { Plus, RefreshCw } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { SiteFormDialog } from "@/components/SiteFormDialog";
import { RatiosDialog } from "@/components/RatiosDialog";
import { LoginPage } from "@/components/LoginPage";
import { Button, ConfirmDialog } from "@/components/ui";
import { errorText, useToast } from "@/components/Toast";
import { api, setConsoleToken } from "@/lib/api";
import { syncSiteBrowserSession } from "@/lib/browserSessionBridge";
import type { Change, NotificationSettings, Site } from "@/lib/types";
import { OverviewPage } from "@/pages/OverviewPage";
import { SitesPage } from "@/pages/SitesPage";
import { DetailPage } from "@/pages/DetailPage";
import { ChangesPage } from "@/pages/ChangesPage";
import { BalancePage } from "@/pages/BalancePage";
import { ChannelsPage } from "@/pages/ChannelsPage";
import { NotificationsPage } from "@/pages/NotificationsPage";

export default function App() {
  const navigate = useNavigate();
  const toast = useToast();
  const [deleteTarget, setDeleteTarget] = useState<Site | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [sites, setSites] = useState<Site[]>([]);
  const [changes, setChanges] = useState<Change[]>([]);
  const [notify, setNotify] = useState<NotificationSettings | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Site | null>(null);
  const [ratiosSite, setRatiosSite] = useState<Site | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [authRequired, setAuthRequired] = useState(false);
  const [authed, setAuthed] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [sitesResp, changesResp, notifyResp] = await Promise.all([
        api.sites(),
        api.changes(50),
        api.notificationSettings(),
      ]);
      const nextSites = sitesResp.data || [];
      setSites(nextSites);
      setChanges(changesResp.data || []);
      setNotify(notifyResp.data || {});
      setSelectedId((prev) => {
        if (prev && nextSites.some((s) => s.id === prev)) return prev;
        return nextSites[0]?.id ?? null;
      });
      setError("");
      // Surface auto-sync results so the user can see when new main-site
      // channels were imported.  The backend fires this on every /api/sites
      // call; we only show a toast when something actually changed.
      const autoSync = (sitesResp as { auto_sync?: unknown }).auto_sync;
      if (Array.isArray(autoSync)) {
        const imported = autoSync.filter(
          (entry) =>
            entry &&
            typeof entry === "object" &&
            (entry as { imported?: number }).imported,
        );
        const failed = autoSync.filter(
          (entry) =>
            entry &&
            typeof entry === "object" &&
            (entry as { status?: string }).status === "fetch_failed",
        );
        if (imported.length > 0) {
          const total = imported.reduce(
            (sum, entry) =>
              sum + ((entry as { imported?: number }).imported || 0),
            0,
          );
          setError(
            `已从主站自动同步 ${total} 个新渠道（共 ${imported.length} 个主站）`,
          );
          // Newly imported sites ship with a session sync request so the
          // upstream browser session gets attached without a manual click.
          // Fire-and-forget; each request resolves on its own and refresh()
          // will pick up the new session_sync_status when it succeeds.
          for (const entry of imported) {
            const requests = (
              entry as { session_sync_requests?: unknown[] }
            ).session_sync_requests;
            if (!Array.isArray(requests)) continue;
            for (const req of requests) {
              if (
                req &&
                typeof req === "object" &&
                typeof (req as { site_id?: unknown }).site_id === "number"
              ) {
                void syncSiteBrowserSession(
                  (req as { site_id: number }).site_id,
                );
              }
            }
          }
        } else if (failed.length > 0) {
          const firstMessage =
            (failed[0] as { message?: string }).message || "读取失败";
          setError(`主站渠道自动同步失败：${firstMessage}`);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    api
      .authStatus()
      .then((s) => {
        if (!mounted) return;
        setAuthRequired(!!s.auth_required);
        setAuthed(!!s.authenticated);
      })
      .catch(() => {
        // 状态接口都拿不到时退化为不拦截，避免把用户锁在门外
        if (!mounted) return;
        setAuthRequired(false);
        setAuthed(true);
      })
      .finally(() => {
        if (mounted) setAuthReady(true);
      });
    const onUnauth = () => {
      setAuthed(false);
      setAuthRequired(true);
    };
    window.addEventListener("console-unauthorized", onUnauth);
    return () => {
      mounted = false;
      window.removeEventListener("console-unauthorized", onUnauth);
    };
  }, []);

  useEffect(() => {
    if (!authReady) return;
    if (authRequired && !authed) return;
    refresh();
    const timer = window.setInterval(() => {
      refresh().catch(() => {});
    }, 15000);
    return () => window.clearInterval(timer);
  }, [refresh, authReady, authRequired, authed]);

  async function handleLogout() {
    try {
      await api.logout();
      toast.success("已退出登录");
    } catch {
      // 登出即使网络失败也要本地清 token，但仍要让用户知道服务端没收到
      toast.info("已在本机退出（服务端未确认）");
    }
    setConsoleToken("");
    setAuthed(false);
    setAuthRequired(true);
  }

  async function handleCheck(site: Site) {
    await toast.run(
      async () => {
        const firstResult = await api.checkSite(site.id);
        if (firstResult.success) {
          await refresh();
          return;
        }
        const canSyncBrowser =
          firstResult.browser_sync_required &&
          site.platform === "sub2api" &&
          site.auth_mode === "browser";
        if (!canSyncBrowser) {
          throw new Error(firstResult.message || "检测失败");
        }

        const syncResult = await syncSiteBrowserSession(site.id);
        await refresh();
        if (syncResult.status !== "ready") {
          throw new Error(
            syncResult.message ||
              syncResult.error_code ||
              "请先在浏览器登录并同步",
          );
        }

        const retryResult = await api.checkSite(site.id);
        await refresh();
        if (!retryResult.success) {
          throw new Error(retryResult.message || "同步后检测仍然失败");
        }
      },
      { success: `已检测「${site.name}」`, failure: `检测「${site.name}」失败` },
    );
  }

  async function handleSessionSync(site: Site) {
    try {
      const result = await syncSiteBrowserSession(site.id);
      await refresh();
      if (result.status === "ready") {
        toast.success(`渠道「${site.name}」登录态已同步`);
        return;
      }
      toast.info(result.message || result.error_code || "登录态同步未完成");
    } catch (err) {
      toast.error(errorText(err, "登录态同步失败"));
    }
  }

  // 删除走自绘确认框：window.confirm 在内嵌 WebView 里会被直接抑制，
  // 表现为「点了没反应」；自绘弹窗能显示进行中与失败原因。
  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    setDeleteError("");
    try {
      await api.deleteSite(deleteTarget.id);
      await refresh();
      toast.success(`已删除渠道「${deleteTarget.name}」`);
      setDeleteTarget(null);
    } catch (err) {
      const message = errorText(err, "删除失败");
      setDeleteError(message);
      toast.error(message);
    } finally {
      setDeleteBusy(false);
    }
  }

  function handleView(site: Site) {
    setSelectedId(site.id);
    navigate(`/detail/${site.id}`);
  }

  const tableHandlers = {
    onView: handleView,
    onRatios: (site: Site) => setRatiosSite(site),
    onCheck: handleCheck,
    onSyncSession: handleSessionSync,
    onEdit: (site: Site) => {
      setEditing(site);
      setFormOpen(true);
    },
    onDelete: (site: Site) => {
      setDeleteError("");
      setDeleteTarget(site);
    },
  };

  if (!authReady) {
    return (
      <div className="flex min-h-screen items-center justify-center text-[13px] text-ink-muted">
        <span className="inline-flex items-center gap-2">
          <span className="skeleton inline-block h-1.5 w-1.5 rounded-full" />
          正在恢复会话…
        </span>
      </div>
    );
  }

  if (authRequired && !authed) {
    return (
      <LoginPage
        onSuccess={() => {
          setAuthed(true);
          setLoading(true);
          refresh();
        }}
      />
    );
  }

  return (
    <AppShell
      siteCount={sites.length}
      onLogout={authRequired ? handleLogout : undefined}
      actions={
        <>
          <Button
            variant="secondary"
            aria-label="刷新数据"
            title="刷新数据"
            onClick={() => refresh()}
          >
            <RefreshCw size={13} />
            <span className="hidden sm:inline">刷新</span>
          </Button>
          <Button
            variant="brand"
            aria-label="添加渠道"
            title="添加渠道"
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
          >
            <Plus size={13} />
            <span className="hidden sm:inline">添加渠道</span>
          </Button>
        </>
      }
    >
      {error ? (
        <div className="mb-6 rounded-[var(--radius-md)] border border-danger-fg/30 bg-danger-bg px-4 py-3 text-[13px] text-danger-fg">
          <div className="font-semibold">无法连接后端 API</div>
          <div className="mt-0.5 opacity-90">
            {error}。请确认后端已启动（<code className="font-mono">python app.py</code>，默认 :8000）。
          </div>
        </div>
      ) : null}
      {loading ? (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="skeleton h-[88px] w-full rounded-[var(--radius-md)]" />
            ))}
          </div>
          <div className="skeleton h-[320px] w-full rounded-[var(--radius-lg)]" />
        </div>
      ) : (
        <Routes>
          <Route
            path="/"
            element={
              <OverviewPage
                sites={sites}
                changes={changes}
                selectedId={selectedId}
                {...tableHandlers}
              />
            }
          />
          <Route
            path="/sites"
            element={
              <SitesPage
                sites={sites}
                selectedId={selectedId}
                {...tableHandlers}
              />
            }
          />
          <Route
            path="/detail"
            element={
              <DetailPage
                sites={sites}
                selectedId={selectedId}
                onSelect={(id) => {
                  setSelectedId(id);
                  navigate(`/detail/${id}`);
                }}
              />
            }
          />
          <Route
            path="/detail/:id"
            element={
              <DetailPage
                sites={sites}
                selectedId={selectedId}
                onSelect={(id) => {
                  setSelectedId(id);
                  navigate(`/detail/${id}`);
                }}
              />
            }
          />
          <Route
            path="/changes"
            element={<ChangesPage changes={changes} sites={sites} />}
          />
          <Route path="/balance" element={<BalancePage sites={sites} />} />
          <Route path="/channels" element={<ChannelsPage />} />
          <Route
            path="/notifications"
            element={
              <NotificationsPage settings={notify} onReload={refresh} />
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      )}

      <SiteFormDialog
        open={formOpen}
        site={editing}
        onClose={() => setFormOpen(false)}
        onSaved={refresh}
        onEditSite={(siteId) => {
          const target = sites.find((site) => site.id === siteId);
          if (!target) {
            toast.info("渠道列表正在刷新，请稍后重试");
            return;
          }
          setEditing(target);
          setFormOpen(true);
        }}
      />
      <RatiosDialog
        open={!!ratiosSite}
        site={ratiosSite}
        onClose={() => setRatiosSite(null)}
      />
      <ConfirmDialog
        open={!!deleteTarget}
        title="删除渠道"
        message={
          <>
            确认删除渠道「<b>{deleteTarget?.name}</b>」？
            <br />
            该渠道的历史快照与变化记录会一并删除，且不可撤销。
          </>
        }
        confirmLabel="删除"
        danger
        busy={deleteBusy}
        error={deleteError}
        onConfirm={confirmDelete}
        onCancel={() => {
          setDeleteTarget(null);
          setDeleteError("");
        }}
      />
    </AppShell>
  );
}
