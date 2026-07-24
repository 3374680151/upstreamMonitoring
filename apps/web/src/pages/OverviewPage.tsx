import { StatCard } from "@/components/StatCard";
import { Panel } from "@/components/Panel";
import { SiteTable } from "@/components/SiteTable";
import { ChangeTable } from "@/components/ChangeTable";
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
}: {
  sites: Site[];
  changes: Change[];
  selectedId: number | null;
  onView: (site: Site) => void;
  onRatios: (site: Site) => void;
  onCheck: (site: Site) => void;
  onEdit: (site: Site) => void;
  onDelete: (site: Site) => void;
}) {
  const enabled = sites.filter((s) => s.enabled).length;
  const ok = sites.filter((s) => s.status === "ok").length;
  const failed = sites.filter((s) =>
    ["warning", "failed"].includes(String(s.status)),
  ).length;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-serif text-2xl font-extrabold text-[var(--color-text-primary)] md:text-3xl">
          上游分组倍率监控
        </h1>
        <p className="mt-1 max-w-3xl text-sm text-[var(--color-text-muted)]">
          定时采集 NewAPI / sub2api 上游分组倍率，发现分组、倍率、描述变化，并支持邮件与企业微信推送。
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard label="监控站点" value={sites.length} />
        <StatCard label="启用中" value={enabled} />
        <StatCard label="正常" value={ok} />
        <StatCard label="异常" value={failed} accent={failed > 0} />
        <StatCard label="最近变化" value={changes.length} hint="最近 50 条" />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="站点概览" subtitle="最近检测状态和分组数量">
          <SiteTable
            sites={sites.slice(0, 6)}
            selectedId={selectedId}
            onView={onView}
            onRatios={onRatios}
            onCheck={onCheck}
            onEdit={onEdit}
            onDelete={onDelete}
          />
        </Panel>
        <Panel title="最近变化" subtitle="最新倍率和分组变化">
          <ChangeTable changes={changes.slice(0, 8)} sites={sites} />
        </Panel>
      </div>
    </div>
  );
}
