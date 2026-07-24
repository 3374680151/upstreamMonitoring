import type { Site } from "@/lib/types";
import { fmtTime, platformLabel, statusTone, truthy } from "@/lib/format";
import { Badge } from "./Badge";
import { Button } from "./ui";

export function SiteTable({
  sites,
  selectedId,
  onView,
  onRatios,
  onCheck,
  onEdit,
  onDelete,
}: {
  sites: Site[];
  selectedId?: number | null;
  onView: (site: Site) => void;
  onRatios: (site: Site) => void;
  onCheck: (site: Site) => void;
  onEdit: (site: Site) => void;
  onDelete: (site: Site) => void;
}) {
  if (!sites.length) {
    return (
      <div className="py-10 text-center text-sm text-[var(--color-text-muted)]">
        暂无站点。点击「添加站点」开始配置。
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[900px] text-left text-sm">
        <thead>
          <tr className="border-b border-[var(--color-border-subtle)] text-xs font-semibold text-[var(--color-text-muted)]">
            <th className="pb-2">站点</th>
            <th className="pb-2">状态</th>
            <th className="pb-2">认证/隐藏</th>
            <th className="pb-2">分组</th>
            <th className="pb-2">上次检测</th>
            <th className="pb-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {sites.map((site) => {
            const authCount = Number(site.current_login_groups_count || 0);
            const publicCount = Number(site.current_groups_count || 0);
            const hiddenCount = Math.max(0, authCount - publicCount);
            const selected = site.id === selectedId;
            return (
              <tr
                key={site.id}
                className={`border-b border-[var(--color-border-subtle)] last:border-0 hover:bg-[var(--color-surface-hover)] ${
                  selected ? "bg-[var(--color-surface)]" : ""
                }`}
              >
                <td className="py-3 pr-3">
                  <div className="font-bold text-[var(--color-text-primary)]">
                    {site.name}
                  </div>
                  <div className="text-[11px] text-[var(--color-text-soft)]">
                    {platformLabel(site)} · {site.base_url}
                  </div>
                </td>
                <td className="py-3 pr-3">
                  <Badge tone={statusTone(site.status)} dot>
                    {site.status}
                  </Badge>
                </td>
                <td className="py-3 pr-3">
                  <div className="flex flex-wrap gap-1">
                    {hiddenCount ? (
                      <Badge tone="warning">{hiddenCount} 个隐藏</Badge>
                    ) : (
                      <span className="text-xs text-[var(--color-text-soft)]">无</span>
                    )}
                    {site.platform === "sub2api" ? (
                      <Badge tone="info">用户登录</Badge>
                    ) : truthy(site.login_enabled) ? (
                      <Badge tone="info">认证增强</Badge>
                    ) : null}
                  </div>
                </td>
                <td className="py-3 pr-3 tabular-nums font-semibold">
                  {site.current_groups_count || 0}
                </td>
                <td className="py-3 pr-3 text-[var(--color-text-muted)]">
                  {fmtTime(site.last_check_at)}
                </td>
                <td className="py-3">
                  <div className="flex flex-wrap gap-1.5">
                    <Button variant="primary" onClick={() => onRatios(site)}>
                      查看倍率
                    </Button>
                    <Button variant="secondary" onClick={() => onView(site)}>
                      详情
                    </Button>
                    <Button variant="secondary" onClick={() => onCheck(site)}>
                      检测
                    </Button>
                    <Button variant="secondary" onClick={() => onEdit(site)}>
                      编辑
                    </Button>
                    <Button variant="danger" onClick={() => onDelete(site)}>
                      删除
                    </Button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
