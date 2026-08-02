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
    return <span className="text-ink-muted">{text}</span>;
  }
  const [, prefix, rawOld, rawNew, suffix] = match;
  const oldNum = Number(rawOld);
  const newNum = Number(rawNew);
  const up = Number.isFinite(oldNum) && Number.isFinite(newNum) && newNum > oldNum;
  const down =
    Number.isFinite(oldNum) && Number.isFinite(newNum) && newNum < oldNum;
  // Lower ratio = cheaper for the user = good (green); higher = more expensive (red).
  const tone = down
    ? "text-success-fg"
    : up
      ? "text-danger-fg"
      : "text-ink-strong";
  const Icon = down ? TrendingDown : up ? TrendingUp : ArrowRight;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {prefix.trim() ? (
        <span className="text-ink-muted">{prefix.trim()}</span>
      ) : null}
      <span className="tabular text-ink-soft line-through decoration-line">
        {rawOld}
      </span>
      <Icon size={12} className={tone} aria-hidden />
      <span className={`font-semibold tabular ${tone}`}>{rawNew}</span>
      {suffix.trim() ? (
        <span className="text-ink-muted">{suffix.trim()}</span>
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
      <div className="flex flex-col items-center gap-1 py-8 text-center">
        <div className="font-serif text-[14px] font-semibold text-ink-strong">
          暂无变化
        </div>
        <p className="text-[12px] text-ink-muted">
          系统每次检测后会把变化的分组倍率写到这里。
        </p>
      </div>
    );
  }
  return (
    <div className="priceai-scrollbar min-w-0 overflow-x-auto pb-1">
      <table className="w-full min-w-max table-auto text-left text-[13px]">
        <thead className="sticky top-0 z-10 bg-panel">
          <tr className="border-b border-line text-[11.5px] font-semibold tracking-[0.04em] text-ink-soft uppercase">
            <th className="whitespace-nowrap pb-2.5 pr-3">时间</th>
            {showSite ? (
              <th className="whitespace-nowrap pb-2.5 pr-3">渠道</th>
            ) : null}
            <th className="whitespace-nowrap pb-2.5 pr-3">类型</th>
            <th className="whitespace-nowrap pb-2.5 pr-3">分组</th>
            <th className="whitespace-nowrap pb-2.5">变化</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((change) => (
            <tr
              key={change.id}
              className="border-b border-line-soft transition-colors duration-[var(--motion-fast)] last:border-0 hover:bg-sunken-hover"
            >
              <td className="py-3 pr-3 align-top text-[11.5px] text-ink-muted">
                <TimeCell value={change.created_at} />
              </td>
              {showSite ? (
                <td className="py-3 pr-3 align-top font-medium text-ink-strong">
                  {siteNameById(sites, change.site_id)}
                </td>
              ) : null}
              <td className="py-3 pr-3 align-top">
                <Badge tone={changeTone(change)}>
                  {changeTypeLabel(change.change_type)}
                </Badge>
              </td>
              <td className="py-3 pr-3 align-top font-medium text-ink">
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
    <span className="whitespace-nowrap tabular leading-[1.35]">
      <span className="block">{date}</span>
      {time ? <span className="block text-ink-soft">{time}</span> : null}
    </span>
  );
}
