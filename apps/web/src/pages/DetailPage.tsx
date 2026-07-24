import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Badge } from "@/components/Badge";
import { Panel } from "@/components/Panel";
import { StatCard } from "@/components/StatCard";
import { ChangeTable } from "@/components/ChangeTable";
import { Select } from "@/components/ui";
import { api } from "@/lib/api";
import {
  changeTone,
  changeTypeLabel,
  fmtTime,
  platformLabel,
  ratioLabel,
  statusTone,
  truthy,
} from "@/lib/format";
import type { Change, Site } from "@/lib/types";

export function DetailPage({
  sites,
  selectedId,
  onSelect,
}: {
  sites: Site[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  const params = useParams();
  const routeId = params.id ? Number(params.id) : null;
  const activeId = routeId || selectedId;
  const site = sites.find((s) => s.id === activeId) || null;
  const [siteChanges, setSiteChanges] = useState<Change[]>([]);

  useEffect(() => {
    if (routeId && routeId !== selectedId) {
      onSelect(routeId);
    }
  }, [routeId, selectedId, onSelect]);

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

  if (!sites.length) {
    return (
      <div className="rounded-2xl border border-dashed border-[var(--color-border)] p-10 text-center text-sm text-[var(--color-text-muted)]">
        还没有站点。请先在「站点监控」中添加。
      </div>
    );
  }

  if (!site) {
    return (
      <div className="space-y-4">
        <Select
          value=""
          onChange={(e) => onSelect(Number(e.target.value))}
        >
          <option value="">选择站点</option>
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

  const modeNote =
    site.platform === "sub2api"
      ? site.auth_mode === "token"
        ? "当前站点使用导入登录态检测该账号实际可见的分组倍率；适合开启 Turnstile 的上游。"
        : "当前站点使用 sub2api 普通用户账号登录，检测该账号实际可见的分组倍率和用户专属倍率。"
      : truthy(site.login_enabled)
        ? "当前站点已开启认证增强监控，检测时会优先使用系统访问令牌采集该账号可见的隐藏用户分组或专属分组。"
        : "当前站点只监控公开 /api/user/groups。若该站存在特殊分组，可在编辑站点中开启认证增强监控。";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-2xl font-extrabold text-[var(--color-text-primary)]">
            站点详情
          </h1>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">
            当前倍率、隐藏分组与历史变化
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-[var(--color-text-muted)]">查看站点</span>
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
      </div>

      <div className="flex flex-wrap items-start gap-4">
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--color-surface)] text-lg font-black text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)]">
          {site.name.slice(0, 1)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-xl font-extrabold text-[var(--color-text-primary)]">
              {site.name}
            </h2>
            <Badge tone={statusTone(site.status)} dot>
              {site.status}
            </Badge>
            <Badge tone="neutral">{platformLabel(site)}</Badge>
            {truthy(site.enabled) ? (
              <Badge tone="success">启用中</Badge>
            ) : (
              <Badge tone="warning">已停用</Badge>
            )}
          </div>
          <p className="mt-1 font-mono text-xs text-[var(--color-text-soft)]">
            {site.base_url}
          </p>
        </div>
      </div>

      {site.last_error ? (
        <div className="rounded-xl bg-[var(--color-danger-bg)] px-4 py-3 text-sm text-[var(--color-danger-text)]">
          {site.last_error}
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="监控间隔" value={`${site.interval_minutes} 分`} />
        <StatCard label="公开分组" value={site.current_groups_count || 0} />
        <StatCard
          label="认证分组"
          value={site.current_login_groups_count || 0}
        />
        <StatCard
          label="连续失败"
          value={site.consecutive_failures || 0}
          accent={Number(site.consecutive_failures || 0) > 0}
        />
        <StatCard label="上次检测" value={fmtTime(site.last_check_at)} />
        <StatCard label="下次检测" value={fmtTime(site.next_check_at)} />
      </div>

      <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-info-bg)] px-4 py-3 text-sm text-[var(--color-info-text)]">
        {modeNote}
      </div>

      {site.login_last_error ? (
        <div className="rounded-xl bg-[var(--color-warning-bg)] px-4 py-3 text-sm text-[var(--color-warning-text)]">
          认证错误：{site.login_last_error}
        </div>
      ) : null}

      {hiddenGroups.length ? (
        <Panel title="认证后新增分组" subtitle={`${hiddenGroups.length} 个隐藏分组`}>
          <div className="space-y-2">
            {hiddenGroups.map(([name, item]) => (
              <div
                key={name}
                className="flex items-center justify-between gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-panel-soft)] px-3 py-2"
              >
                <div>
                  <div className="font-semibold text-[var(--color-text-primary)]">
                    {name}
                  </div>
                  <div className="text-xs text-[var(--color-text-muted)]">
                    {item.desc || "-"}
                  </div>
                </div>
                <div className="font-extrabold tabular-nums">
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
                className="flex items-center justify-between gap-3 rounded-xl border border-[var(--color-border)] px-3 py-2.5 hover:bg-[var(--color-surface-hover)]"
              >
                <div className="min-w-0">
                  <div className="font-bold text-[var(--color-text-primary)]">
                    {name}
                  </div>
                  <div className="truncate text-xs text-[var(--color-text-muted)]">
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
                <div className="shrink-0 text-lg font-extrabold tabular-nums text-[var(--color-text-primary)]">
                  {ratioLabel(item)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-sm text-[var(--color-text-muted)]">暂无倍率数据</div>
        )}
      </Panel>

      <Panel title="该站历史变化" subtitle="最近 50 条">
        <div className="mb-3 space-y-2 md:hidden">
          {siteChanges.slice(0, 10).map((change) => (
            <div
              key={change.id}
              className="rounded-xl border border-[var(--color-border)] px-3 py-2"
            >
              <div className="flex items-center justify-between gap-2">
                <Badge tone={changeTone(change)}>
                  {changeTypeLabel(change.change_type)}
                </Badge>
                <time className="text-[11px] text-[var(--color-text-soft)]">
                  {fmtTime(change.created_at)}
                </time>
              </div>
              <div className="mt-1 font-semibold">{change.group_name || "-"}</div>
              <div className="text-xs text-[var(--color-text-muted)]">
                {change.message || "-"}
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
