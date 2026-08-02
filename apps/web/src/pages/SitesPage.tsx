import { useMemo, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { SiteTable } from "@/components/SiteTable";
import { Input, Select } from "@/components/ui";
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
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState("");
  const [platform, setPlatform] = useState("all");

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

  return (
    <div className="upstream-rise space-y-6">
      <PageHeader
        title="渠道监控"
        subtitle="盯你的上游渠道站点，每个渠道单独设置平台类型、监控间隔和认证方式。"
      />

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
              className="w-36"
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
