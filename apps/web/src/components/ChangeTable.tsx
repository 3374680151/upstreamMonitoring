import type { Change, Site } from "@/lib/types";
import {
  changeTone,
  changeTypeLabel,
  fmtTime,
  siteNameById,
} from "@/lib/format";
import { Badge } from "./Badge";

export function ChangeTable({
  changes,
  sites,
  showSite = true,
}: {
  changes: Change[];
  sites: Site[];
  showSite?: boolean;
}) {
  if (!changes.length) {
    return (
      <div className="py-8 text-center text-sm text-[var(--color-text-muted)]">
        暂无变化记录
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead>
          <tr className="border-b border-[var(--color-border-subtle)] text-xs font-semibold text-[var(--color-text-muted)]">
            <th className="pb-2">时间</th>
            {showSite ? <th className="pb-2">站点</th> : null}
            <th className="pb-2">类型</th>
            <th className="pb-2">分组</th>
            <th className="pb-2">变化</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((change) => (
            <tr
              key={change.id}
              className="border-b border-[var(--color-border-subtle)] last:border-0 hover:bg-[var(--color-surface-hover)]"
            >
              <td className="py-3 pr-3 tabular-nums text-[var(--color-text-muted)]">
                {fmtTime(change.created_at)}
              </td>
              {showSite ? (
                <td className="py-3 pr-3 font-semibold text-[var(--color-text-primary)]">
                  {siteNameById(sites, change.site_id)}
                </td>
              ) : null}
              <td className="py-3 pr-3">
                <Badge tone={changeTone(change)}>
                  {changeTypeLabel(change.change_type)}
                </Badge>
              </td>
              <td className="py-3 pr-3">{change.group_name || "-"}</td>
              <td className="py-3 text-[var(--color-text-muted)]">
                {change.message || "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
