import { Pencil, Power, PowerOff, RefreshCw } from "lucide-react";
import { Badge } from "@/components/Badge";
import { normalizedChannelStatus } from "@/lib/sub2apiChannel";
import type { Channel } from "@/lib/types";

const iconButtonClass =
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-sm)] border border-line bg-panel text-ink-muted transition hover:border-line-strong hover:bg-sunken-hover hover:text-ink-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] disabled:pointer-events-none disabled:opacity-50";

function statusMeta(channel: Channel) {
  const status = normalizedChannelStatus(channel);
  if (status === "active") {
    return { label: "运行中", tone: "success" as const };
  }
  if (status === "disabled") {
    return { label: "已停用", tone: "warning" as const };
  }
  return { label: "异常", tone: "danger" as const };
}

function multiplier(value?: number | null): string {
  return value === undefined || value === null ? "--" : `x${value}`;
}

function modelSummary(channel: Channel): string {
  const pricing = channel.model_pricing || [];
  const modelCount = new Set(
    pricing.flatMap((item) => item.models || []).filter(Boolean),
  ).size;
  const platforms = [
    ...new Set(pricing.map((item) => item.platform).filter(Boolean)),
  ];
  if (!pricing.length) return "未配置";
  return `${modelCount} 模型 · ${platforms.join(" / ") || "未标注平台"}`;
}

function billingSummary(channel: Channel): string {
  const modes = [
    ...new Set(
      (channel.model_pricing || [])
        .map((item) => item.billing_mode)
        .filter(Boolean),
    ),
  ];
  const source = channel.billing_model_source || "channel";
  return `${source} · ${modes.join(" / ") || "未配置"}`;
}

export function Sub2ApiChannelTable({
  channels,
  busyChannelIds,
  canEdit,
  canToggle,
  onEdit,
  onToggle,
  onRefresh,
}: {
  channels: Channel[];
  busyChannelIds: Set<number>;
  canEdit: boolean;
  canToggle: boolean;
  onEdit: (channel: Channel) => void;
  onToggle: (channel: Channel) => void;
  onRefresh: (channel: Channel) => void;
}) {
  return (
    <div className="priceai-scrollbar max-h-[calc(100vh-18rem)] overflow-auto rounded-[var(--radius-sm)]">
      <table className="w-full min-w-[980px] table-fixed text-left text-sm">
        <colgroup>
          <col className="w-[185px]" />
          <col className="w-[85px]" />
          <col className="w-[135px]" />
          <col className="w-[150px]" />
          <col className="w-[160px]" />
          <col className="w-[140px]" />
          <col className="w-[125px]" />
        </colgroup>
        <thead className="sticky top-0 z-10 bg-panel">
          <tr className="border-b border-line-soft text-[12.5px] font-semibold text-ink-muted">
            <th className="pb-2">渠道</th>
            <th className="pb-2">状态</th>
            <th className="pb-2">分组</th>
            <th className="pb-2">分组倍率</th>
            <th className="pb-2">模型定价</th>
            <th className="pb-2">计费</th>
            <th className="pb-2 text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          {channels.map((channel) => {
            const meta = statusMeta(channel);
            const busy = busyChannelIds.has(channel.id);
            const active = normalizedChannelStatus(channel) === "active";
            const groups = channel.groups || [];
            return (
              <tr
                key={channel.id}
                className="border-b border-line-soft last:border-0 hover:bg-sunken-hover"
              >
                <td className="max-w-0 py-3 pr-3">
                  <div className="truncate font-bold text-ink-strong">
                    {channel.name || `#${channel.id}`}
                  </div>
                  <div className="truncate text-[11px] text-ink-soft">
                    #{channel.id}
                    {channel.description ? ` · ${channel.description}` : ""}
                  </div>
                </td>
                <td className="py-3 pr-3">
                  <Badge tone={meta.tone} dot>
                    {meta.label}
                  </Badge>
                </td>
                <td className="max-w-0 py-3 pr-3">
                  <div className="flex flex-wrap gap-1">
                    {groups.map((group) => (
                      <Badge key={group.id} tone="info">
                        {group.name}
                      </Badge>
                    ))}
                    {!groups.length ? (
                      <span className="text-[12.5px] text-ink-soft">--</span>
                    ) : null}
                  </div>
                </td>
                <td className="max-w-0 py-3 pr-3">
                  <div className="flex flex-wrap gap-1 tabular-nums">
                    {groups.map((group) => (
                      <Badge key={group.id} tone="success">
                        {group.name} {multiplier(group.rate_multiplier)}
                      </Badge>
                    ))}
                    {!groups.length ? (
                      <span className="text-[12.5px] text-ink-soft">--</span>
                    ) : null}
                  </div>
                </td>
                <td className="max-w-0 py-3 pr-3">
                  <div
                    className="truncate text-[12.5px] text-ink"
                    title={modelSummary(channel)}
                  >
                    {modelSummary(channel)}
                  </div>
                  <div className="mt-0.5 text-[11px] tabular-nums text-ink-soft">
                    {(channel.model_pricing || []).length} 条定价规则
                  </div>
                </td>
                <td className="max-w-0 py-3 pr-3">
                  <div
                    className="truncate text-[12.5px] text-ink"
                    title={billingSummary(channel)}
                  >
                    {billingSummary(channel)}
                  </div>
                </td>
                <td className="py-3">
                  <div className="flex min-h-8 items-center justify-end gap-1.5">
                    <button
                      type="button"
                      className={iconButtonClass}
                      title="刷新主站数据"
                      aria-label={`刷新渠道 ${channel.name || channel.id}`}
                      disabled={busy}
                      onClick={() => onRefresh(channel)}
                    >
                      <RefreshCw size={15} className={busy ? "animate-spin" : ""} />
                    </button>
                    {canToggle ? (
                      <button
                        type="button"
                        className={iconButtonClass}
                        title={active ? "停用渠道" : "启用渠道"}
                        aria-label={`${active ? "停用" : "启用"}渠道 ${channel.name || channel.id}`}
                        disabled={busy}
                        onClick={() => onToggle(channel)}
                      >
                        {active ? <PowerOff size={15} /> : <Power size={15} />}
                      </button>
                    ) : null}
                    {canEdit ? (
                      <button
                        type="button"
                        className={iconButtonClass}
                        title="编辑渠道配置"
                        aria-label={`编辑渠道 ${channel.name || channel.id}`}
                        disabled={busy}
                        onClick={() => onEdit(channel)}
                      >
                        <Pencil size={15} />
                      </button>
                    ) : null}
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
