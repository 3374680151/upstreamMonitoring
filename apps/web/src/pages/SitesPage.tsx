import { useMemo, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { SiteTable } from "@/components/SiteTable";
import { Button, Input, Select } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api";
import type { Site } from "@/lib/types";

export function SitesPage({
  sites,
  selectedId,
  onView,
  onRatios,
  onCheck,
  onEdit,
  onDelete,
  onSyncSession,
}: {
  sites: Site[];
  selectedId: number | null;
  onView: (site: Site) => void;
  onRatios: (site: Site) => void;
  onCheck: (site: Site) => void;
  onEdit: (site: Site) => void;
  onDelete: (site: Site) => void;
  onSyncSession: (site: Site) => void | Promise<void>;
}) {
  const toast = useToast();
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState("");
  const [platform, setPlatform] = useState("all");
  const [syncingAll, setSyncingAll] = useState(false);
  const [syncingBrowser, setSyncingBrowser] = useState(false);
  const [syncResult, setSyncResult] = useState<string>("");

  const filtered = useMemo(() => {
    const q = keyword.trim().toLowerCase();
    return sites.filter((site) => {
      if (q && !`${site.name} ${site.base_url}`.toLowerCase().includes(q)) {
        return false;
      }
      if (status && site.status !== status) return false;
      if (platform !== "all" && site.platform !== platform) return false;
      return true;
    });
  }, [sites, keyword, status, platform]);

  async function syncAllFromMain() {
    if (syncingAll) return;
    setSyncingAll(true);
    setSyncResult("");
    try {
      const [adminsRes, sitesRes] = await Promise.all([
        api.adminSites(),
        api.sites(),
      ]);
      const admins = adminsRes.data || [];
      const localSites = sitesRes.data || [];
      if (!admins.length) {
        const message = "还没有主站，请先在「主站监控」添加主站";
        setSyncResult(message);
        toast.info(message);
        return;
      }
      const localByPlatformUrl = new Map<string, Map<string, Site>>();
      for (const s of localSites) {
        const platform = String(s.platform || "");
        const url = String(s.base_url || "").replace(/\/+$/, "").toLowerCase();
        if (!url) continue;
        if (!localByPlatformUrl.has(platform)) {
          localByPlatformUrl.set(platform, new Map());
        }
        localByPlatformUrl.get(platform)!.set(url, s);
      }
      const summary: string[] = [];
      const failureDetails: string[] = [];
      let totalCreated = 0;
      let totalSkipped = 0;
      let totalFailed = 0;
      for (const admin of admins) {
        const platform = String(admin.platform || "");
        const localByUrl = localByPlatformUrl.get(platform) || new Map();
        const res = await api.channelCandidates(admin.id);
        const candidates = res.data || [];
        const created: string[] = [];
        const skipped: string[] = [];
        const failed: string[] = [];
        for (const cand of candidates) {
          const url = String(cand.base_url || "")
            .replace(/\/+$/, "")
            .toLowerCase();
          if (!url) continue;
          if (localByUrl.has(url)) {
            skipped.push(url);
            continue;
          }
          try {
            const createdRes = await api.createSite({
              name: cand.name || url,
              platform: platform as "newapi" | "sub2api",
              base_url: url,
              interval_minutes: 60,
              login_enabled: false,
              auth_mode: platform === "sub2api" ? "browser" : "token",
              login_username: "",
              login_password: "",
              access_token: "",
              refresh_token: "",
              token_expires_at: "",
              access_user_id: "",
              enabled: true,
            });
            if (createdRes.success && createdRes.id) {
              if ((createdRes as { existed?: boolean }).existed) {
                skipped.push(`#${createdRes.id}`);
              } else {
                created.push(`#${createdRes.id}`);
              }
              localByUrl.set(url, { id: createdRes.id } as Site);
            } else {
              failed.push(url);
              failureDetails.push(
                `${admin.name || `#${admin.id}`} ${cand.name || url}：未返回 ID（后端 success=false）`,
              );
            }
          } catch (err) {
            const reason = err instanceof Error ? err.message : String(err);
            failed.push(url);
            failureDetails.push(
              `${admin.name || `#${admin.id}`} ${cand.name || url}：${reason}`,
            );
          }
        }
        totalCreated += created.length;
        totalSkipped += skipped.length;
        totalFailed += failed.length;
        summary.push(
          `${admin.name || `#${admin.id}`}：新增 ${created.length}、已存在 ${skipped.length}、失败 ${failed.length}`,
        );
      }
      const detailMessage = `新增 ${totalCreated}、已存在 ${totalSkipped}、失败 ${totalFailed}\n` +
        summary.join("\n") +
        (failureDetails.length ? `\n\n失败明细：\n${failureDetails.join("\n")}` : "");
      setSyncResult(detailMessage);
      toast.success(`主站同步完成：${summary.join("；")}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setSyncResult(`主站同步失败：${message}`);
      toast.error(`主站同步失败：${message}`);
    } finally {
      setSyncingAll(false);
    }
  }

  async function syncSub2ApiBrowserSessions() {
    if (syncingBrowser) return;
    const targets = sites.filter(
      (site) =>
        site.platform === "sub2api" && site.auth_mode === "browser",
    );
    if (!targets.length) {
      toast.info("暂无配置浏览器登录态的 sub2api 渠道");
      return;
    }
    setSyncingBrowser(true);
    try {
      for (const site of targets) {
        await onSyncSession(site);
      }
      toast.success(`已完成 ${targets.length} 个 sub2api 渠道的登录态同步`);
    } finally {
      setSyncingBrowser(false);
    }
  }

  return (
    <div className="upstream-rise flex flex-col gap-6 md:gap-8">
      <PageHeader
        title="渠道监控"
        subtitle="盯你的上游渠道站点，每个渠道单独设置平台类型、监控间隔和认证方式。"
        action={
          <div className="flex flex-wrap items-center gap-2">
            {sites.some(
              (site) =>
                site.platform === "sub2api" && site.auth_mode === "browser",
            ) ? (
              <Button
                variant="secondary"
                onClick={() => void syncSub2ApiBrowserSessions()}
                loading={syncingBrowser}
                title="仅同步 sub2api 渠道的浏览器登录态"
              >
                同步登录态
              </Button>
            ) : null}
            <Button
              variant="brand"
              onClick={() => void syncAllFromMain()}
              loading={syncingAll}
              title="按主站所有渠道的 base_url 增量创建本地监控站点"
            >
              从主站同步
            </Button>
          </div>
        }
      />

      {syncResult ? (
        <div className="rounded-[var(--radius-sm)] border border-line bg-panel-soft px-3 py-2 text-sm text-ink">
          <div className="mb-1 flex items-center justify-between">
            <span className="font-semibold text-ink-strong">主站同步结果</span>
            <button
              className="text-[11px] text-ink-muted hover:text-ink-strong"
              onClick={() => setSyncResult("")}
            >
              关闭
            </button>
          </div>
          <pre className="whitespace-pre-wrap text-[12px] leading-relaxed text-ink-muted">
            {syncResult}
          </pre>
        </div>
      ) : null}

      <Panel
        title="渠道列表"
        subtitle={`${filtered.length} / ${sites.length} 条`}
        action={
          <div className="flex flex-wrap gap-2">
            <Input
              className="w-48"
              type="search"
              placeholder="搜索渠道或地址"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
            <Select
              className="w-32"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="">全部状态</option>
              <option value="ok">正常</option>
              <option value="warning">警告</option>
              <option value="failed">异常</option>
              <option value="unknown">未知</option>
            </Select>
            <Select
              className="w-32"
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
            >
              <option value="all">全部平台</option>
              <option value="newapi">NewAPI</option>
              <option value="sub2api">sub2api</option>
            </Select>
          </div>
        }
      >
        <SiteTable
          sites={filtered}
          selectedId={selectedId}
          onView={onView}
          onRatios={onRatios}
          onCheck={onCheck}
          onEdit={onEdit}
          onDelete={onDelete}
          onSyncSession={onSyncSession}
          groupByPlatform
        />
      </Panel>
    </div>
  );
}
