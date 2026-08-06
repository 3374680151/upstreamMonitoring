import { useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Server,
  Zap,
} from "lucide-react";
import { StatCard } from "@/components/StatCard";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { SiteTable } from "@/components/SiteTable";
import { ChangeTable } from "@/components/ChangeTable";
import { MainSiteHealthPanel } from "@/components/MainSiteHealthPanel";
import { Select } from "@/components/ui";
import { truthy } from "@/lib/format";
import type { Change, Site } from "@/lib/types";

export function OverviewPage({
  sites,
  changes,
  selectedId,
  onView,
  onRatios,
  onCheck,
  onEdit,
  onDelete,
  onSyncSession,
}: {
  sites: Site[];
  changes: Change[];
  selectedId: number | null;
  onView: (site: Site) => void;
  onRatios: (site: Site) => void;
  onCheck: (site: Site) => void;
  onEdit: (site: Site) => void;
  onDelete: (site: Site) => void;
  onSyncSession: (site: Site) => void | Promise<void>;
}) {
  const [platformFilter, setPlatformFilter] = useState("all");
  const leftPanelRef = useRef<HTMLDivElement>(null);
  const [overviewPanelHeight, setOverviewPanelHeight] = useState<number | null>(
    null,
  );
  const enabled = sites.filter((site) => truthy(site.enabled)).length;
  const ok = sites.filter(
    (site) => truthy(site.enabled) && site.status === "ok",
  ).length;
  const failed = sites.filter(
    (site) =>
      truthy(site.enabled) &&
      ["warning", "failed"].includes(String(site.status)),
  ).length;
  const overviewSites =
    platformFilter === "all"
      ? sites
      : sites.filter((site) => site.platform === platformFilter);

  useEffect(() => {
    const node = leftPanelRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;

    const syncHeight = () => {
      if (window.innerWidth < 1400) {
        setOverviewPanelHeight(null);
        return;
      }
      setOverviewPanelHeight(Math.round(node.getBoundingClientRect().height));
    };
    const observer = new ResizeObserver(syncHeight);
    observer.observe(node);
    syncHeight();
    window.addEventListener("resize", syncHeight);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", syncHeight);
    };
  }, [overviewSites.length, platformFilter]);

  return (
    <div className="upstream-rise flex flex-col gap-6 md:gap-8">
      <PageHeader
        large
        title="上游分组倍率监控"
        subtitle="定时采集 NewAPI / sub2api 上游分组倍率，发现分组、倍率、描述变化，并支持邮件与企业微信推送。"
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
        <StatCard
          label="监控渠道"
          value={sites.length}
          tone="info"
          icon={<Server size={17} />}
        />
        <StatCard
          label="启用中"
          value={enabled}
          tone="brand"
          icon={<Zap size={17} />}
        />
        <StatCard
          label="正常"
          value={ok}
          tone="brand"
          icon={<CheckCircle2 size={17} />}
        />
        <StatCard
          label="异常"
          value={failed}
          tone={failed > 0 ? "danger" : "neutral"}
          accent={failed > 0}
          icon={<AlertTriangle size={17} />}
        />
        <StatCard
          label="最近变化"
          value={changes.length}
          hint="最近 50 条"
          tone="warning"
          icon={<Activity size={17} />}
          className="col-span-2 sm:col-span-1"
        />
      </div>

      <div className="grid min-w-0 gap-6 min-[1400px]:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] min-[1400px]:items-start">
        <div ref={leftPanelRef} className="min-w-0">
          <Panel
            className="flex h-full min-w-0 flex-col"
            title="渠道概览"
            subtitle={`${overviewSites.length} 个渠道 · 最近检测状态和分组数量`}
            action={
              <label className="flex items-center gap-2 text-[12.5px] font-medium text-ink-strong">
                <span>平台分类</span>
                <Select
                  className="w-32"
                  value={platformFilter}
                  onChange={(event) => setPlatformFilter(event.target.value)}
                >
                  <option value="all">全部平台</option>
                  <option value="newapi">NewAPI</option>
                  <option value="sub2api">sub2api</option>
                </Select>
              </label>
            }
          >
            <div className="priceai-scrollbar min-h-0 max-h-[430px] overflow-y-auto pr-1">
              <SiteTable
                sites={overviewSites}
                selectedId={selectedId}
                onView={onView}
                onRatios={onRatios}
                onCheck={onCheck}
                onEdit={onEdit}
                onDelete={onDelete}
                onSyncSession={onSyncSession}
                groupByPlatform
              />
            </div>
          </Panel>
        </div>

        <div
          className="min-w-0"
          style={
            overviewPanelHeight
              ? { height: `${overviewPanelHeight}px` }
              : undefined
          }
        >
          <Panel
            className="flex h-full min-w-0 flex-col"
            title="最近变化"
            subtitle="最新倍率和分组变化"
          >
            <div className="priceai-scrollbar min-h-0 min-w-0 max-h-[430px] flex-1 overflow-y-auto pr-1 min-[1400px]:max-h-none">
              <ChangeTable changes={changes} sites={sites} />
            </div>
          </Panel>
        </div>
      </div>

      {/* 主站（你自己的中转站）健康总览 */}
      <MainSiteHealthPanel />
    </div>
  );
}
