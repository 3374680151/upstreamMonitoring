import { ArrowRight, TrendingDown, TrendingUp } from "lucide-react";
import type { Change, Site } from "@/lib/types";
import {
  changeDisplayMessage,
  changeTone,
  changeTypeLabel,
  fmtTimeParts,
  siteNameById,
} from "@/lib/format";
import { Badge } from "./Badge";

/** Render a change message, highlighting `old -> new` numeric transitions. */
function ChangeValue({ message }: { message?: string | null }) {
  const text = message || "-";
  const match = text.match(/^(.*?)([\d.]+)\s*(?:->|→|=>)\s*([\d.]+)(.*)$/);
  if (!match) {
    return <span className="text-[var(--color-text-muted)]">{text}</span>;
  }
  const [, prefix, rawOld, rawNew, suffix] = match;
  const oldNum = Number(rawOld);
  const newNum = Number(rawNew);
  const up = Number.isFinite(oldNum) && Number.isFinite(newNum) && newNum > oldNum;
  const down =
    Number.isFinite(oldNum) && Number.isFinite(newNum) && newNum < oldNum;
  // Lower ratio = cheaper for the user = good (green); higher = more expensive (red).
  const tone = down
    ? "text-[var(--color-success-text)]"
    : up
      ? "text-[var(--color-danger-text)]"
      : "text-[var(--color-text-primary)]";
  const Icon = down ? TrendingDown : up ? TrendingUp : ArrowRight;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {prefix.trim() ? (
        <span className="text-[var(--color-text-muted)]">{prefix.trim()}</span>
      ) : null}
      <span className="tabular-nums text-[var(--color-text-soft)] line-through decoration-[var(--color-border)]">
        {rawOld}
      </span>
      <Icon size={13} className={tone} aria-hidden />
      <span className={`font-bold tabular-nums ${tone}`}>{rawNew}</span>
      {suffix.trim() ? (
        <span className="text-[var(--color-text-muted)]">{suffix.trim()}</span>
      ) : null}
    </span>
  );
}

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
    <div className="priceai-scrollbar min-w-0 overflow-x-auto pb-1">
      <table className="w-full min-w-max table-auto text-left text-sm">
        <thead className="sticky top-0 z-10 bg-[var(--color-panel)]">
          <tr className="border-b border-[var(--color-border-subtle)] text-xs font-semibold text-[var(--color-text-muted)]">
            <th className="whitespace-nowrap pb-2 pr-3">时间</th>
            {showSite ? <th className="whitespace-nowrap pb-2 pr-3">渠道</th> : null}
            <th className="whitespace-nowrap pb-2 pr-3">类型</th>
            <th className="whitespace-nowrap pb-2 pr-3">分组</th>
            <th className="whitespace-nowrap pb-2">变化</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((change) => (
            <tr
              key={change.id}
              className="border-b border-[var(--color-border-subtle)] transition-colors last:border-0 hover:bg-[var(--color-surface-hover)]"
            >
              <td className="py-3 pr-3 align-top text-xs text-[var(--color-text-muted)]">
                <TimeCell value={change.created_at} />
              </td>
              {showSite ? (
                <td className="py-3 pr-3 align-top font-semibold text-[var(--color-text-primary)]">
                  {siteNameById(sites, change.site_id)}
                </td>
              ) : null}
              <td className="py-3 pr-3 align-top">
                <Badge tone={changeTone(change)}>
                  {changeTypeLabel(change.change_type)}
                </Badge>
              </td>
              <td className="py-3 pr-3 align-top font-medium text-[var(--color-text-body)]">
                {change.group_name || "-"}
              </td>
              <td className="py-3 align-top">
                <ChangeValue message={changeDisplayMessage(change)} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TimeCell({ value }: { value?: string | null }) {
  const [date, time] = fmtTimeParts(value);
  return (
    <span className="whitespace-nowrap tabular-nums leading-5">
      <span className="block">{date}</span>
      {time ? <span className="block">{time}</span> : null}
    </span>
  );
}
