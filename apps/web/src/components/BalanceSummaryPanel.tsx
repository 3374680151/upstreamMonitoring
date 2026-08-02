import { Link } from "react-router-dom";
import { Panel } from "@/components/Panel";
import { Badge } from "@/components/Badge";
import { platformLabel } from "@/lib/format";
import { usd, useBalances } from "@/lib/useBalances";
import type { Site } from "@/lib/types";

/**
 * 总览页的「各渠道余额」卡片：只列渠道，不在总览页打上游。
 * 查询统一收在「余额」页（右上角「详情 →」过去）。
 */
export function BalanceSummaryPanel({ sites }: { sites: Site[] }) {
  const { rows, summary } = useBalances(sites);

  return (
    <Panel
      title="各渠道余额"
      subtitle={
        summary.queried
          ? `已查 ${summary.okCount} / ${sites.length} 个渠道${summary.errCount ? ` · ${summary.errCount} 个失败` : ""}`
          : "余额在「余额」页一键查询，这里只列已配置的渠道"
      }
      action={
        <Link
          to="/balance"
          className="rounded-lg px-2 py-1.5 text-sm font-semibold text-[var(--color-brand)] hover:underline"
        >
          详情 →
        </Link>
      }
    >
      {!sites.length ? (
        <div className="py-6 text-center text-sm text-[var(--color-text-muted)]">
          还没有配置渠道站点
        </div>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-xs font-semibold text-[var(--color-text-muted)]">合计余额</span>
            <span className="text-2xl font-extrabold tabular-nums text-[var(--color-text-primary)]">
              {summary.okCount ? usd(summary.total) : "—"}
            </span>
            {summary.errCount ? (
              <Badge tone="warning">{summary.errCount} 个读取失败</Badge>
            ) : null}
          </div>

          <div className="space-y-1.5">
            {sites.map((site) => {
              const row = rows[site.id] || { state: "idle" as const };
              return (
                <div
                  key={site.id}
                  className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-xl border border-[var(--color-border)] px-3 py-2"
                >
                  <div className="flex min-w-0 flex-1 items-center gap-2">
                    <span className="truncate text-sm font-semibold text-[var(--color-text-primary)]">
                      {site.name}
                    </span>
                    <Badge tone="neutral">{platformLabel(site)}</Badge>
                  </div>
                  <div className="shrink-0 text-sm">
                    {row.state === "ok" ? (
                      <span className="font-bold tabular-nums text-[var(--color-text-primary)]">
                        {usd(row.account.balance_usd)}
                      </span>
                    ) : row.state === "loading" ? (
                      <span className="text-xs text-[var(--color-text-muted)]">查询中…</span>
                    ) : row.state === "error" ? (
                      <span
                        className="text-xs text-[var(--color-warning-text)]"
                        title={row.message}
                      >
                        读取失败
                      </span>
                    ) : (
                      <span className="text-xs text-[var(--color-text-soft)]">未查询</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </Panel>
  );
}
