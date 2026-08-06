import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Clock,
  Layers,
  ShieldCheck,
  Timer,
  TrendingDown,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/Badge";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { StatCard } from "@/components/StatCard";
import { ChangeTable } from "@/components/ChangeTable";
import { Button, Select } from "@/components/ui";
import { errorText, useToast } from "@/components/Toast";
import { api } from "@/lib/api";
import {
  changeDisplayMessage,
  changeTone,
  changeTypeLabel,
  fmtTime,
  platformLabel,
  ratioLabel,
  siteStatusLabel,
  siteStatusTone,
  truthy,
} from "@/lib/format";
import { explainUpstreamError } from "@/lib/upstreamError";
import type {
  Change,
  Site,
  SiteAccount,
  SiteDiscoveryLink,
} from "@/lib/types";

function fmtUsd(value?: number | null): string {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return "-";
  }
  return `$${Number(value).toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function DetailPage({
  sites,
  selectedId,
  onSelect,
}: {
  sites: Site[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  const toast = useToast();
  const params = useParams();
  const routeId = params.id ? Number(params.id) : null;
  const activeId = routeId || selectedId;
  const site = sites.find((s) => s.id === activeId) || null;
  const [siteChanges, setSiteChanges] = useState<Change[]>([]);
  const [account, setAccount] = useState<SiteAccount | null>(null);
  const [accountLoading, setAccountLoading] = useState(false);
  const [accountError, setAccountError] = useState<string | null>(null);
  const [accountFetchedAt, setAccountFetchedAt] = useState<string | null>(null);
  const [discoveryLinks, setDiscoveryLinks] = useState<SiteDiscoveryLink[]>([]);

  async function loadAccount() {
    if (!activeId) return;
    setAccountLoading(true);
    setAccountError(null);
    try {
      const resp = await api.siteAccount(activeId);
      setAccount(resp.account || null);
      setAccountFetchedAt(resp.fetched_at || null);
      toast.success("账户额度已更新");
    } catch (err) {
      const message = errorText(err, "读取账户额度失败");
      setAccount(null);
      setAccountError(message);
      toast.error(message);
    } finally {
      setAccountLoading(false);
    }
  }

  useEffect(() => {
    if (routeId) {
      onSelect(routeId);
    }
  }, [routeId, onSelect]);

  useEffect(() => {
    // 切换站点时清空账户面板，避免串号展示
    setAccount(null);
    setAccountError(null);
    setAccountFetchedAt(null);
    setAccountLoading(false);
    setDiscoveryLinks([]);
  }, [activeId]);

  useEffect(() => {
    if (!activeId) {
      setSiteChanges([]);
      return;
    }
    let cancelled = false;
    api
      .siteChanges(activeId, 50)
      .then((resp) => {
        if (!cancelled) setSiteChanges(resp.data || []);
      })
      .catch(() => {
        if (!cancelled) setSiteChanges([]);
      });
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    api
      .siteDiscoveryLinks(activeId)
      .then((resp) => {
        if (!cancelled) setDiscoveryLinks(resp.data || []);
      })
      .catch(() => {
        if (!cancelled) setDiscoveryLinks([]);
      });
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  if (!sites.length) {
    return (
      <div className="flex flex-col items-center gap-1.5 rounded-[var(--radius-lg)] border border-dashed border-line py-14 text-center">
        <div className="font-serif text-[16px] font-semibold text-ink-strong">
          还没有渠道
        </div>
        <p className="max-w-sm text-[12.5px] leading-relaxed text-ink-muted">
          请先到「渠道监控」添加一个上游站点，再回来查看倍率详情。
        </p>
      </div>
    );
  }

  if (!site) {
    return (
      <div className="flex flex-col gap-4">
        <Select
          value=""
          onChange={(e) => onSelect(Number(e.target.value))}
        >
          <option value="">选择渠道</option>
          {sites.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </Select>
      </div>
    );
  }

  const publicGroups = site.current_groups || {};
  const loginGroups = site.current_login_groups || {};
  const activeGroups =
    truthy(site.login_enabled) && Object.keys(loginGroups).length
      ? loginGroups
      : publicGroups;
  const groups = Object.entries(activeGroups).sort(([a], [b]) =>
    a.localeCompare(b, "zh-CN"),
  );
  const hiddenGroups = Object.entries(loginGroups).filter(
    ([name]) => !(name in publicGroups),
  );
  const siteError = site.last_error
    ? explainUpstreamError(site.last_error)
    : null;

  const modeNote =
    site.platform === "sub2api"
      ? site.auth_mode === "token"
        ? "当前渠道使用导入登录态检测该账号实际可见的分组倍率；适合开启 Turnstile 的上游。"
        : "当前渠道使用 sub2api 普通用户账号登录，检测该账号实际可见的分组倍率和用户专属倍率。"
      : truthy(site.login_enabled)
        ? "当前渠道已开启认证增强监控，检测时会优先使用系统访问令牌采集该账号可见的隐藏用户分组或专属分组。"
        : "当前渠道只监控公开 /api/user/groups。若该站存在特殊分组，可在编辑渠道中开启认证增强监控。";

  return (
    <div className="upstream-rise flex flex-col gap-6 md:gap-8">
      <PageHeader
        title="渠道详情"
        subtitle="当前倍率、隐藏分组与历史变化"
        action={
          <label className="flex items-center gap-2 text-[12.5px]">
            <span className="text-ink-muted">查看渠道</span>
            <Select
              className="w-56"
              value={String(site.id)}
              onChange={(e) => onSelect(Number(e.target.value))}
            >
              {sites.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} · {platformLabel(s)}
                </option>
              ))}
            </Select>
          </label>
        }
      />

      <div className="flex flex-wrap items-start gap-4">
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-[var(--radius-md)] bg-sunken font-serif text-[18px] font-semibold text-ink-strong ring-1 ring-line">
          {site.name.slice(0, 1)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-serif text-[22px] font-semibold tracking-[-0.015em] text-ink-strong">
              {site.name}
            </h2>
            <Badge tone={siteStatusTone(site)} dot>
              {siteStatusLabel(site)}
            </Badge>
            <Badge tone="neutral">{platformLabel(site)}</Badge>
          </div>
          <p className="mt-1 font-mono text-[11.5px] text-ink-soft">
            {site.base_url}
          </p>
        </div>
      </div>

      {siteError ? (
        <div className="rounded-[var(--radius-md)] border border-danger-fg/30 bg-danger-bg px-4 py-3 text-[13px] text-danger-fg">
          <div className="font-semibold">错误原因：{siteError.summary}</div>
          <div className="mt-1 break-all font-mono text-[11.5px] opacity-90">
            原始错误：{siteError.raw}
          </div>
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="监控间隔"
          value={`${site.interval_minutes} 分`}
          tone="info"
          icon={<Timer size={17} />}
        />
        <StatCard
          label="公开分组"
          value={site.current_groups_count || 0}
          tone="brand"
          icon={<Layers size={17} />}
        />
        <StatCard
          label="认证分组"
          value={site.current_login_groups_count || 0}
          tone="warning"
          icon={<ShieldCheck size={17} />}
        />
        <StatCard
          label="连续失败"
          value={site.consecutive_failures || 0}
          tone={Number(site.consecutive_failures || 0) > 0 ? "danger" : "neutral"}
          accent={Number(site.consecutive_failures || 0) > 0}
          icon={
            Number(site.consecutive_failures || 0) > 0 ? (
              <XCircle size={17} />
            ) : (
              <TrendingDown size={17} />
            )
          }
        />
        <StatCard
          label="上次检测"
          value={fmtTime(site.last_check_at)}
          tone="neutral"
          icon={<Clock size={17} />}
        />
        <StatCard
          label="下次检测"
          value={fmtTime(site.next_check_at)}
          tone="neutral"
          icon={<Clock size={17} />}
        />
      </div>

      <div className="rounded-[var(--radius-md)] border border-line bg-info-bg px-4 py-3 text-[13px] leading-relaxed text-info-fg">
        {modeNote}
      </div>

      {discoveryLinks.length ? (
        <Panel
          title="发现来源"
          subtitle={`${discoveryLinks.length} 条来源关联`}
        >
          <div className="divide-y divide-line-soft">
            {discoveryLinks.map((link) => (
              <div
                key={`${link.admin_site_id}-${link.channel_id}`}
                className="grid gap-1 py-2.5 sm:grid-cols-[1fr_1fr_auto] sm:items-center"
              >
                <span className="font-semibold text-ink-strong">
                  {link.admin_site_name}
                </span>
                <span className="text-[12px] text-ink-muted">
                  {link.channel_name || `渠道 #${link.channel_id}`}
                </span>
                <code className="break-all font-mono text-[11px] text-ink-soft">
                  {link.upstream_base_url}
                </code>
              </div>
            ))}
          </div>
        </Panel>
      ) : null}

      <Panel
        title="账户额度"
        subtitle={
          site.platform === "sub2api"
            ? "登录后读取 /api/v1/auth/me：余额与订阅用量"
            : "凭系统访问令牌读取 /api/user/self：剩余额度与用量"
        }
        action={
          <Button
            variant="secondary"
            onClick={loadAccount}
            loading={accountLoading}
          >
            {accountLoading ? "查询中…" : account ? "刷新" : "查询账户额度"}
          </Button>
        }
      >
        {accountError ? (
          <div className="rounded-[var(--radius-md)] border border-warning-fg/30 bg-warning-bg px-4 py-3 text-[13px] text-warning-fg">
            {accountError}
          </div>
        ) : account ? (
          <div className="flex flex-col gap-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                label={site.platform === "sub2api" ? "账户余额" : "剩余额度"}
                value={fmtUsd(account.balance_usd)}
                hint={account.username || account.email || undefined}
              />
              {site.platform === "sub2api" ? (
                <>
                  <StatCard
                    label="冻结余额"
                    value={fmtUsd(account.frozen_balance_usd)}
                    accent={false}
                  />
                  <StatCard
                    label="累计充值"
                    value={fmtUsd(account.total_recharged_usd)}
                    accent={false}
                  />
                  <StatCard
                    label="RPM 上限"
                    value={
                      account.rpm_limit === null ||
                      account.rpm_limit === undefined ||
                      account.rpm_limit === 0
                        ? "不限"
                        : String(account.rpm_limit)
                    }
                    accent={false}
                  />
                </>
              ) : (
                <>
                  <StatCard
                    label="已用额度"
                    value={fmtUsd(account.used_usd)}
                    accent={false}
                  />
                  <StatCard
                    label="请求次数"
                    value={
                      account.request_count === null ||
                      account.request_count === undefined
                        ? "-"
                        : Number(account.request_count).toLocaleString("zh-CN")
                    }
                    accent={false}
                  />
                  <StatCard
                    label="用户分组"
                    value={account.group || "-"}
                    accent={false}
                  />
                </>
              )}
            </div>

            {account.subscriptions && account.subscriptions.length ? (
              <div className="space-y-2">
                <div className="t-micro">订阅用量</div>
                {account.subscriptions.map((sub, index) => (
                  <div
                    key={`${sub.name}-${index}`}
                    className="rounded-[var(--radius-md)] border border-line bg-panel-soft px-3 py-2.5"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="font-semibold text-ink-strong">
                        {sub.name || "订阅"}
                      </div>
                      <div className="flex items-center gap-2">
                        {sub.status ? (
                          <Badge tone="neutral">{sub.status}</Badge>
                        ) : null}
                        {sub.expires_at ? (
                          <span className="text-[11.5px] text-ink-soft">
                            到期 {fmtTime(String(sub.expires_at))}
                          </span>
                        ) : null}
                      </div>
                    </div>
                    <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-muted tabular">
                      <span>
                        日 {fmtUsd(sub.daily_usage_usd)}
                        {sub.daily_limit_usd ? ` / ${fmtUsd(sub.daily_limit_usd)}` : ""}
                      </span>
                      <span>
                        周 {fmtUsd(sub.weekly_usage_usd)}
                        {sub.weekly_limit_usd ? ` / ${fmtUsd(sub.weekly_limit_usd)}` : ""}
                      </span>
                      <span>
                        月 {fmtUsd(sub.monthly_usage_usd)}
                        {sub.monthly_limit_usd ? ` / ${fmtUsd(sub.monthly_limit_usd)}` : ""}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            ) : null}

            {accountFetchedAt ? (
              <div className="text-[11.5px] text-ink-soft">
                更新于 {fmtTime(accountFetchedAt)}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="text-[13px] text-ink-muted">
            右上角点击「查询账户额度」，实时读取该渠道登录账号的余额 / 额度。
          </div>
        )}
      </Panel>

      {site.login_last_error ? (
        <div className="rounded-[var(--radius-md)] border border-warning-fg/30 bg-warning-bg px-4 py-3 text-[13px] text-warning-fg">
          认证错误：{site.login_last_error}
        </div>
      ) : null}

      {hiddenGroups.length ? (
        <Panel
          title="认证后新增分组"
          subtitle={`${hiddenGroups.length} 个隐藏分组`}
        >
          <div className="space-y-2">
            {hiddenGroups.map(([name, item]) => (
              <div
                key={name}
                className="flex items-center justify-between gap-3 rounded-[var(--radius-md)] border border-line bg-panel-soft px-3 py-2"
              >
                <div>
                  <div className="font-semibold text-ink-strong">{name}</div>
                  <div className="text-[12px] text-ink-muted">
                    {item.desc || "-"}
                  </div>
                </div>
                <div className="font-serif text-[15.5px] font-semibold tabular">
                  {ratioLabel(item)}
                </div>
              </div>
            ))}
          </div>
        </Panel>
      ) : null}

      <Panel
        title={
          site.platform === "sub2api"
            ? "用户可见分组倍率"
            : truthy(site.login_enabled) && Object.keys(loginGroups).length
              ? "认证分组倍率"
              : "当前公开分组倍率"
        }
        subtitle={`${groups.length} 个分组`}
      >
        {groups.length ? (
          <div className="space-y-2">
            {groups.map(([name, item]) => (
              <div
                key={name}
                className="flex items-center justify-between gap-3 rounded-[var(--radius-md)] border border-line px-3 py-2.5 transition-colors duration-[var(--motion-fast)] hover:bg-sunken-hover"
              >
                <div className="min-w-0">
                  <div className="font-semibold text-ink-strong">{name}</div>
                  <div className="truncate text-[12px] text-ink-muted">
                    {[
                      item.platform,
                      item.status,
                      item.is_exclusive ? "专属" : "",
                      item.desc || "-",
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                </div>
                <div className="shrink-0 font-serif text-[16px] font-semibold tabular text-ink-strong">
                  {ratioLabel(item)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-[13px] text-ink-muted">暂无倍率数据</div>
        )}
      </Panel>

      <Panel title="该站历史变化" subtitle="最近 50 条">
        <div className="mb-3 space-y-2 md:hidden">
          {siteChanges.slice(0, 10).map((change) => (
            <div
              key={change.id}
              className="rounded-[var(--radius-md)] border border-line px-3 py-2"
            >
              <div className="flex items-center justify-between gap-2">
                <Badge tone={changeTone(change)}>
                  {changeTypeLabel(change.change_type)}
                </Badge>
                <time className="text-[11px] text-ink-soft">
                  {fmtTime(change.created_at)}
                </time>
              </div>
              <div className="mt-1 font-semibold">
                {change.group_name || "-"}
              </div>
              <div className="text-[12px] text-ink-muted">
                {changeDisplayMessage(change)}
              </div>
            </div>
          ))}
        </div>
        <div className="hidden md:block">
          <ChangeTable changes={siteChanges} sites={sites} showSite={false} />
        </div>
      </Panel>
    </div>
  );
}
