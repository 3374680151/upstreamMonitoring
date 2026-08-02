import { Fragment } from "react";
import { useState } from "react";
import {
  ChevronDown,
  Eye,
  Gauge,
  Pencil,
  RefreshCw,
  Trash2,
} from "lucide-react";
import type { SessionSyncStatus, Site } from "@/lib/types";
import { isSessionSyncRetryable } from "@/lib/browserSessionBridge";
import {
  fmtTimeParts,
  platformLabel,
  statusLabel,
  statusTone,
  truthy,
} from "@/lib/format";
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
  onSyncSession,
  groupByPlatform = false,
}: {
  sites: Site[];
  selectedId?: number | null;
  onView: (site: Site) => void;
  onRatios: (site: Site) => void;
  onCheck: (site: Site) => void | Promise<void>;
  onEdit: (site: Site) => void;
  onDelete: (site: Site) => void;
  onSyncSession: (site: Site) => void | Promise<void>;
  groupByPlatform?: boolean;
}) {
  const [collapsedPlatforms, setCollapsedPlatforms] = useState<Set<string>>(
    new Set(),
  );
  const [checkingId, setCheckingId] = useState<number | null>(null);
  const [syncingId, setSyncingId] = useState<number | null>(null);

  if (!sites.length) {
    return (
      <div className="py-10 text-center text-sm text-[var(--color-text-muted)]">
        暂无渠道。点击「添加渠道」开始配置。
      </div>
    );
  }
  const sections = groupByPlatform
    ? [
        {
          key: "newapi",
          label: "NewAPI 渠道",
          sites: sites.filter((site) => site.platform === "newapi"),
        },
        {
          key: "sub2api",
          label: "sub2api 渠道",
          sites: sites.filter((site) => site.platform === "sub2api"),
        },
      ].filter((section) => section.sites.length)
    : [{ key: "all", label: "", sites }];

  return (
    <div className="priceai-scrollbar min-w-0 overflow-x-auto pb-1">
      <table className="w-full min-w-max table-auto text-left text-sm">
        <thead>
          <tr className="border-b border-[var(--color-border-subtle)] text-xs font-semibold text-[var(--color-text-muted)]">
            <th className="whitespace-nowrap pb-2 pr-3">渠道</th>
            <th className="whitespace-nowrap pb-2 pr-3">状态</th>
            <th className="whitespace-nowrap pb-2 pr-3">认证/隐藏</th>
            <th className="whitespace-nowrap pb-2 pr-3">分组</th>
            <th className="whitespace-nowrap pb-2 pr-3">上次检测</th>
            <th className="whitespace-nowrap pb-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {sections.map((section) => (
            <Fragment key={section.key}>
              {groupByPlatform ? (
                <tr key={`${section.key}-header`}>
                  <td colSpan={6} className="pt-3 first:pt-0">
                    <button
                      type="button"
                      className={`flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-xs outline-none transition duration-200 hover:brightness-[0.97] focus-visible:ring-2 focus-visible:ring-current/40 ${
                        section.key === "newapi"
                          ? "bg-[var(--color-success-bg)] text-[var(--color-success-text)]"
                          : "bg-[var(--color-info-bg)] text-[var(--color-info-text)]"
                      }`}
                      aria-expanded={!collapsedPlatforms.has(section.key)}
                      onClick={() => {
                        const next = new Set(collapsedPlatforms);
                        if (next.has(section.key)) next.delete(section.key);
                        else next.add(section.key);
                        setCollapsedPlatforms(next);
                      }}
                    >
                      <span className="flex items-center gap-2 font-bold">
                        <ChevronDown
                          size={14}
                          className={`transition-transform duration-200 ${
                            collapsedPlatforms.has(section.key)
                              ? "-rotate-90"
                              : "rotate-0"
                          }`}
                          aria-hidden
                        />
                        {section.label}
                      </span>
                      <span className="rounded-full bg-[var(--color-panel)]/60 px-2 py-0.5 tabular-nums font-semibold">
                        {section.sites.length} 个渠道
                      </span>
                    </button>
                  </td>
                </tr>
              ) : null}
              {!groupByPlatform || !collapsedPlatforms.has(section.key)
                ? section.sites.map((site) => {
            const authCount = Number(site.current_login_groups_count || 0);
            const publicCount = Number(site.current_groups_count || 0);
            const hiddenCount = Math.max(0, authCount - publicCount);
            const selected = site.id === selectedId;
            return (
              <tr
                key={site.id}
                className={`group border-b border-[var(--color-border-subtle)] transition-colors duration-150 last:border-0 hover:bg-[var(--color-surface-hover)] ${
                  selected ? "bg-[var(--color-surface)]" : ""
                }`}
              >
                <td className="min-w-0 py-3 pr-3 align-middle">
                  <div className="flex items-center gap-2.5">
                    <span
                      className={`h-8 w-0.5 shrink-0 rounded-full transition-colors duration-150 ${
                        selected
                          ? "bg-[var(--color-brand)]"
                          : "bg-transparent group-hover:bg-[var(--color-border-muted)]"
                      }`}
                      aria-hidden
                    />
                    <div className="min-w-0">
                      <div className="font-bold text-[var(--color-text-primary)]">
                        {site.name}
                      </div>
                      <div className="truncate text-[11px] text-[var(--color-text-soft)]">
                        {platformLabel(site)} · {site.base_url}
                      </div>
                    </div>
                  </div>
                </td>
                <td className="py-3 pr-3 align-middle">
                  <Badge tone={statusTone(site.status)} dot>
                    {statusLabel(site.status)}
                  </Badge>
                </td>
                <td className="py-3 pr-3 align-middle">
                  <div className="flex flex-wrap gap-1">
                    {hiddenCount ? (
                      <Badge tone="warning">{hiddenCount} 个隐藏</Badge>
                    ) : (
                      <span className="text-xs text-[var(--color-text-soft)]">无</span>
                    )}
                    {site.auth_mode === "browser" ? (
                        <Badge tone={sessionSyncTone(site.session_sync_status)}>
                          {sessionSyncLabel(site.session_sync_status)}
                        </Badge>
                    ) : site.platform === "sub2api" ? (
                        <Badge tone="info">用户登录</Badge>
                    ) : truthy(site.login_enabled) ? (
                      <Badge tone="info">认证增强</Badge>
                    ) : null}
                  </div>
                </td>
                <td className="py-3 pr-3 align-middle tabular-nums font-semibold">
                  {site.current_groups_count || 0}
                </td>
                <td className="py-3 pr-3 align-middle text-xs text-[var(--color-text-muted)]">
                  <TimeCell value={site.last_check_at} />
                </td>
                <td className="py-3 align-middle">
                  <div className="flex flex-nowrap items-center justify-end gap-1.5">
                    {site.auth_mode === "browser" &&
                    isSessionSyncRetryable(site.session_sync_status) ? (
                      <Button
                        variant="secondary"
                        size="sm"
                        className="h-8 shrink-0 whitespace-nowrap"
                        aria-label="同步登录态"
                        title="同步登录态"
                        loading={syncingId === site.id}
                        onClick={async () => {
                          setSyncingId(site.id);
                          try {
                            await onSyncSession(site);
                          } finally {
                            setSyncingId(null);
                          }
                        }}
                      >
                        {syncingId === site.id ? null : <RefreshCw size={13} />}
                        同步登录态
                      </Button>
                    ) : null}
                    <Button
                      variant="brand"
                      size="sm"
                      className="h-8 shrink-0 whitespace-nowrap"
                      aria-label="查看倍率"
                      title="查看倍率"
                      onClick={() => onRatios(site)}
                    >
                      <Gauge size={13} />
                      查看倍率
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      className="h-8 shrink-0 whitespace-nowrap"
                      aria-label="查看详情"
                      title="查看详情"
                      onClick={() => onView(site)}
                    >
                      <Eye size={13} />
                      详情
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      className="h-8 shrink-0 whitespace-nowrap"
                      aria-label="立即检测"
                      title="立即检测"
                      loading={checkingId === site.id}
                      onClick={async () => {
                        // 检测要打上游、可能好几秒：本行先转圈，别让人以为没点上
                        setCheckingId(site.id);
                        try {
                          await onCheck(site);
                        } finally {
                          setCheckingId(null);
                        }
                      }}
                    >
                      {checkingId === site.id ? null : <RefreshCw size={13} />}
                      检测
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      className="h-8 shrink-0 whitespace-nowrap"
                      aria-label="编辑渠道"
                      title="编辑渠道"
                      onClick={() => onEdit(site)}
                    >
                      <Pencil size={13} />
                      编辑
                    </Button>
                    <Button
                      variant="danger"
                      size="sm"
                      className="h-8 shrink-0 whitespace-nowrap"
                      onClick={() => onDelete(site)}
                      aria-label="删除"
                      title="删除"
                    >
                      <Trash2 size={13} />
                      删除
                    </Button>
                  </div>
                </td>
              </tr>
            );
                  })
                : null}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function sessionSyncLabel(status?: SessionSyncStatus): string {
  return {
    not_requested: "待同步",
    pending: "等待扩展",
    validating: "验证中",
    ready: "登录态已同步",
    no_session: "没有登录态",
    expired: "登录态已失效",
    permission_required: "需要站点权限",
    extension_unavailable: "扩展未连接",
    failed: "同步失败",
  }[status || "not_requested"];
}

function sessionSyncTone(
  status?: SessionSyncStatus,
): "success" | "warning" | "danger" | "info" {
  if (status === "ready") return "success";
  if (status === "pending" || status === "validating") return "info";
  if (status === "no_session" || status === "permission_required") {
    return "warning";
  }
  return "danger";
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
