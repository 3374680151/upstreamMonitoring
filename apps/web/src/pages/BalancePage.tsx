import { Coins, RefreshCw, ServerCrash, Wallet } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { Badge } from "@/components/Badge";
import { StatCard } from "@/components/StatCard";
import { Button } from "@/components/ui";
import { fmtTime, platformLabel } from "@/lib/format";
import { usd, useBalances } from "@/lib/useBalances";
import type { Site, SiteAccount } from "@/lib/types";

/** 各平台的补充信息（NewAPI 看请求数/分组，sub2api 看冻结/充值/RPM） */
function extraText(site: Site, account: SiteAccount): string {
  const parts: string[] = [];
  if (site.platform === "sub2api") {
    if (account.frozen_balance_usd != null) parts.push(`冻结 ${usd(account.frozen_balance_usd)}`);
    if (account.total_recharged_usd != null) parts.push(`充值 ${usd(account.total_recharged_usd)}`);
    if (account.rpm_limit != null && account.rpm_limit !== 0) parts.push(`RPM ${account.rpm_limit}`);
  } else {
    if (account.request_count != null) {
      parts.push(`${Number(account.request_count).toLocaleString("zh-CN")} 次请求`);
    }
    if (account.group) parts.push(`分组 ${account.group}`);
  }
  return parts.join(" · ") || "—";
}

export function BalancePage({ sites }: { sites: Site[] }) {
  const { rows, busy, queryOne, queryAll, summary } = useBalances(sites);

  if (!sites.length) {
    return (
      <div className="upstream-rise space-y-6">
        <PageHeader title="余额" subtitle="按你配置的渠道站点，用各自登录态查询账户余额" />
        <Panel title="余额" subtitle="暂无渠道">
          <div className="py-10 text-center text-sm text-[var(--color-text-muted)]">
            还没有配置渠道。请先在「渠道监控」添加站点并填写登录信息（NewAPI 系统访问令牌 / sub2api 账号或登录态）。
          </div>
        </Panel>
      </div>
    );
  }

  return (
    <div className="upstream-rise space-y-6">
      <PageHeader
        title="余额"
        subtitle="按你配置的渠道站点，用各自登录态查询：NewAPI /api/user/self · sub2api /api/v1/auth/me"
      />

      <div className="grid gap-3 sm:grid-cols-3">
        <StatCard
          label="合计余额"
          value={summary.okCount ? usd(summary.total) : "—"}
          hint={summary.okCount ? `${summary.okCount} 个站点已查询` : "点下方「一键查询」"}
          tone="brand"
          icon={<Wallet size={18} />}
        />
        <StatCard
          label="已查询 / 渠道总数"
          value={`${summary.okCount} / ${sites.length}`}
          hint="仅成功读取的计入合计"
          tone="info"
          icon={<Coins size={18} />}
        />
        <StatCard
          label="读取失败"
          value={summary.errCount}
          hint="未配置登录或上游返回异常"
          tone={summary.errCount ? "warning" : "neutral"}
          accent={!!summary.errCount}
          icon={<ServerCrash size={18} />}
        />
      </div>

      <Panel
        title="各渠道余额"
        subtitle="点「查询」单独刷新某个渠道，或点右侧「一键查询」拉取全部"
        action={
          <Button onClick={queryAll} loading={busy}>
            {busy ? null : <RefreshCw size={14} />}
            {busy ? "查询中…" : "一键查询"}
          </Button>
        }
      >
        <div className="priceai-scrollbar overflow-x-auto pb-1">
          <table className="w-full min-w-max table-auto text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--color-border-subtle)] text-xs font-semibold text-[var(--color-text-muted)]">
                <th className="pb-2">渠道</th>
                <th className="pb-2">平台</th>
                <th className="pb-2 text-right">余额</th>
                <th className="pb-2 text-right">已用</th>
                <th className="pb-2">补充信息</th>
                <th className="pb-2">更新时间</th>
                <th className="pb-2"></th>
              </tr>
            </thead>
            <tbody>
              {sites.map((site) => {
                const row = rows[site.id] || { state: "idle" as const };
                return (
                  <tr
                    key={site.id}
                    className="border-b border-[var(--color-border-subtle)] last:border-0 hover:bg-[var(--color-surface-hover)]"
                  >
                    <td className="py-3 pr-3">
                      <div className="font-bold text-[var(--color-text-primary)]">{site.name}</div>
                      <div className="font-mono text-[11px] text-[var(--color-text-soft)]">
                        {site.base_url}
                      </div>
                    </td>
                    <td className="py-3 pr-3">
                      <Badge tone="neutral">{platformLabel(site)}</Badge>
                    </td>
                    <td className="py-3 pr-3 text-right">
                      {row.state === "ok" ? (
                        <span className="font-bold tabular-nums text-[var(--color-text-primary)]">
                          {usd(row.account.balance_usd)}
                        </span>
                      ) : row.state === "loading" ? (
                        <span className="text-xs text-[var(--color-text-muted)]">查询中…</span>
                      ) : row.state === "error" ? (
                        <Badge tone="warning">失败</Badge>
                      ) : (
                        <span className="text-xs text-[var(--color-text-soft)]">未查询</span>
                      )}
                    </td>
                    <td className="py-3 pr-3 text-right tabular-nums text-[var(--color-text-muted)]">
                      {row.state === "ok" ? usd(row.account.used_usd) : "—"}
                    </td>
                    <td className="py-3 pr-3 text-xs text-[var(--color-text-muted)]">
                      {row.state === "ok" ? (
                        extraText(site, row.account)
                      ) : row.state === "error" ? (
                        <span className="text-[var(--color-warning-text)]">{row.message}</span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-3 pr-3 text-[11px] text-[var(--color-text-soft)]">
                      {row.state === "ok" && row.fetchedAt ? fmtTime(row.fetchedAt) : "—"}
                    </td>
                    <td className="py-3">
                      <Button
                        variant="secondary"
                        onClick={() => queryOne(site)}
                        loading={row.state === "loading"}
                        disabled={busy}
                      >
                        查询
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* sub2api 订阅用量：仅在查到订阅时展示 */}
        {sites.some((s) => {
          const row = rows[s.id];
          return row?.state === "ok" && row.account.subscriptions?.length;
        }) ? (
          <div className="mt-4 space-y-2">
            <div className="text-xs font-semibold text-[var(--color-text-muted)]">订阅用量</div>
            {sites.map((site) => {
              const row = rows[site.id];
              if (row?.state !== "ok" || !row.account.subscriptions?.length) return null;
              return row.account.subscriptions.map((sub, index) => (
                <div
                  key={`${site.id}-${sub.name}-${index}`}
                  className="rounded-xl border border-[var(--color-border)] px-3 py-2.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-bold text-[var(--color-text-primary)]">
                      {site.name} · {sub.name || "订阅"}
                    </div>
                    <div className="flex items-center gap-2">
                      {sub.status ? <Badge tone="neutral">{sub.status}</Badge> : null}
                      {sub.expires_at ? (
                        <span className="text-xs text-[var(--color-text-soft)]">
                          到期 {fmtTime(String(sub.expires_at))}
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs tabular-nums text-[var(--color-text-muted)]">
                    <span>
                      日 {usd(sub.daily_usage_usd)}
                      {sub.daily_limit_usd ? ` / ${usd(sub.daily_limit_usd)}` : ""}
                    </span>
                    <span>
                      周 {usd(sub.weekly_usage_usd)}
                      {sub.weekly_limit_usd ? ` / ${usd(sub.weekly_limit_usd)}` : ""}
                    </span>
                    <span>
                      月 {usd(sub.monthly_usage_usd)}
                      {sub.monthly_limit_usd ? ` / ${usd(sub.monthly_limit_usd)}` : ""}
                    </span>
                  </div>
                </div>
              ));
            })}
          </div>
        ) : null}
      </Panel>
    </div>
  );
}
