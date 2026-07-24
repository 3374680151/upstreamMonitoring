import { useMemo, useState } from "react";
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
}: {
  sites: Site[];
  selectedId: number | null;
  onView: (site: Site) => void;
  onRatios: (site: Site) => void;
  onCheck: (site: Site) => void;
  onEdit: (site: Site) => void;
  onDelete: (site: Site) => void;
}) {
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState("");

  const filtered = useMemo(() => {
    const q = keyword.trim().toLowerCase();
    return sites.filter((site) => {
      if (q && !`${site.name} ${site.base_url}`.toLowerCase().includes(q)) {
        return false;
      }
      if (status && site.status !== status) return false;
      return true;
    });
  }, [sites, keyword, status]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-serif text-2xl font-extrabold text-[var(--color-text-primary)]">
          站点监控
        </h1>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">
          少量上游手动维护，每个站点单独设置平台类型、监控间隔和认证方式。
        </p>
      </div>

      <Panel
        title="站点列表"
        subtitle={`${filtered.length} / ${sites.length} 条`}
        action={
          <div className="flex flex-wrap gap-2">
            <Input
              className="w-48"
              type="search"
              placeholder="搜索站点或地址"
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
        />
      </Panel>
    </div>
  );
}
