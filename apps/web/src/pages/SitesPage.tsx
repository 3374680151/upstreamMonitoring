import { useMemo, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { SiteTable } from "@/components/SiteTable";
import { Button, Input, Select } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api";
import { truthy } from "@/lib/format";
import type { Site } from "@/lib/types";

type MainSiteSyncRow = {
  admin_site_id?: number;
  platform?: string;
  status?: string;
  channels_count?: number;
  groups_count?: number;
  message?: string;
};

export function SitesPage({
  sites,
  selectedId,
  onView,
  onRatios,
  onCheck,
  onEdit,
  onDelete,
  onSyncSession,
  onRefresh,
}: {
  sites: Site[];
  selectedId: number | null;
  onView: (site: Site) => void;
  onRatios: (site: Site) => void;
  onCheck: (site: Site) => void;
  onEdit: (site: Site) => void;
  onDelete: (site: Site) => void;
  onSyncSession: (site: Site) => void | Promise<void>;
  onRefresh: () => void | Promise<void>;
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
      const enabled = truthy(site.enabled);
      if (status === "disabled" && enabled) return false;
      if (
        status &&
        status !== "disabled" &&
        (!enabled || site.status !== status)
      ) {
        return false;
      }
      if (platform !== "all" && site.platform !== platform) return false;
      return true;
    });
  }, [sites, keyword, status, platform]);

  async function syncAllFromMain() {
    if (syncingAll) return;
    setSyncingAll(true);
    setSyncResult("");
    try {
      const result = await api.syncMainSites();
      if (result.success === false) {
        throw new Error("主站同步失败");
      }
      await onRefresh();

      const rows = (result.data || []) as MainSiteSyncRow[];
      const syncedRows = rows.filter((row) => row.status === "synced");
      const failedRows = rows.filter(
        (row) =>
          row.status === "sync_failed" ||
          row.status === "fetch_failed" ||
          row.status === "error",
      );
      const summary: string[] = [];
      if (result.channels_changed) summary.push("渠道数据已更新");
      if (result.groups_changed) summary.push("分组数据已更新");
      if (result.imported) summary.push(`新增监控 ${result.imported}`);
      if (result.reenabled) summary.push(`恢复 ${result.reenabled}`);
      if (result.disabled) summary.push(`停用 ${result.disabled}`);
      if (result.deleted) summary.push(`删除 ${result.deleted}`);
      if (result.conflicts) summary.push(`平台冲突 ${result.conflicts}`);
      if (!summary.length) summary.push("渠道和分组已是最新");

      const siteDetails = syncedRows.map(
        (row) =>
          `主站 #${row.admin_site_id ?? "-"}：${row.channels_count ?? 0} 个渠道、${row.groups_count ?? 0} 个分组`,
      );
      const failureDetails = failedRows.map(
        (row) => `主站 #${row.admin_site_id ?? "-"}：${row.message || "读取失败"}`,
      );
      setSyncResult(
        [...summary, ...siteDetails, ...failureDetails].join("\n"),
      );
      if (result.failed) {
        toast.info(`主站同步完成，但有 ${result.failed} 个主站读取失败`);
      } else if (result.conflicts) {
        toast.info(`主站同步完成，但有 ${result.conflicts} 个平台冲突`);
      } else {
        toast.success(`主站同步完成：${summary.join("、")}`);
      }
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
        truthy(site.enabled) &&
        site.platform === "sub2api" &&
        site.auth_mode === "browser",
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
                truthy(site.enabled) &&
                site.platform === "sub2api" &&
                site.auth_mode === "browser",
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
              title="同步所有主站的完整渠道、分组和本地来源关联"
            >
              同步主站
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
              <option value="disabled">停用</option>
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
