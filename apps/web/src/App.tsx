import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { SiteFormDialog } from "@/components/SiteFormDialog";
import { RatiosDialog } from "@/components/RatiosDialog";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import type { Change, NotificationSettings, Site } from "@/lib/types";
import { OverviewPage } from "@/pages/OverviewPage";
import { SitesPage } from "@/pages/SitesPage";
import { DetailPage } from "@/pages/DetailPage";
import { ChangesPage } from "@/pages/ChangesPage";
import { NotificationsPage } from "@/pages/NotificationsPage";

export default function App() {
  const navigate = useNavigate();
  const [sites, setSites] = useState<Site[]>([]);
  const [changes, setChanges] = useState<Change[]>([]);
  const [notify, setNotify] = useState<NotificationSettings | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Site | null>(null);
  const [ratiosSite, setRatiosSite] = useState<Site | null>(null);

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
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(() => {
      refresh().catch(() => {});
    }, 15000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function handleCheck(site: Site) {
    try {
      await api.checkSite(site.id);
      await refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleDelete(site: Site) {
    if (!confirm(`确认删除站点「${site.name}」？`)) return;
    try {
      await api.deleteSite(site.id);
      await refresh();
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
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
    onEdit: (site: Site) => {
      setEditing(site);
      setFormOpen(true);
    },
    onDelete: handleDelete,
  };

  return (
    <AppShell
      siteCount={sites.length}
      actions={
        <>
          <Button variant="secondary" onClick={() => refresh()}>
            刷新
          </Button>
          <Button
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
          >
            添加站点
          </Button>
        </>
      }
    >
      {error ? (
        <div className="mb-4 rounded-xl bg-[var(--color-danger-bg)] px-4 py-3 text-sm text-[var(--color-danger-text)]">
          API 连接失败：{error}。请确认后端已启动（python app.py，默认 :8000）。
        </div>
      ) : null}
      {loading ? (
        <div className="py-16 text-center text-sm text-[var(--color-text-muted)]">
          加载中...
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
      />
      <RatiosDialog
        open={!!ratiosSite}
        site={ratiosSite}
        onClose={() => setRatiosSite(null)}
      />
    </AppShell>
  );
}
