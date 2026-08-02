import { Fragment } from "react";
import { useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  Eye,
  Gauge,
  MoreHorizontal,
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

const PLATFORM_SECTION: Record<
  string,
  { label: string; tone: "success" | "info" }
> = {
  newapi: { label: "NewAPI 渠道", tone: "success" },
  sub2api: { label: "sub2api 渠道", tone: "info" },
};

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
    return <EmptySites />;
  }
  const sections = groupByPlatform
    ? (["newapi", "sub2api"] as const)
        .map((key) => ({
          key,
          label: PLATFORM_SECTION[key].label,
          tone: PLATFORM_SECTION[key].tone,
          sites: sites.filter((site) => site.platform === key),
        }))
        .filter((section) => section.sites.length)
    : [{ key: "all", label: "", tone: "info" as const, sites }];

  return (
    <div className="priceai-scrollbar min-w-0 overflow-x-auto pb-1">
      <table className="w-full min-w-max table-auto text-left text-[13px]">
        <thead>
          <tr className="border-b border-line text-[11.5px] font-semibold tracking-[0.04em] text-ink-soft uppercase">
            <th className="whitespace-nowrap pb-2.5 pr-3">渠道</th>
            <th className="whitespace-nowrap pb-2.5 pr-3">状态</th>
            <th className="whitespace-nowrap pb-2.5 pr-3">认证 / 隐藏</th>
            <th className="whitespace-nowrap pb-2.5 pr-3">分组</th>
            <th className="whitespace-nowrap pb-2.5 pr-3">上次检测</th>
            <th className="whitespace-nowrap pb-2.5">操作</th>
          </tr>
        </thead>
        <tbody>
          {sections.map((section) => (
            <Fragment key={section.key}>
              {groupByPlatform ? (
                <tr key={`${section.key}-header`}>
                  <td colSpan={6} className="pt-4 first:pt-0">
                    <button
                      type="button"
                      className={`group/section flex w-full items-center justify-between rounded-[var(--radius-sm)] border border-line bg-panel-soft px-3 py-2 text-left text-[12px] outline-none transition-[border-color,background-color] duration-[var(--motion-base)] hover:border-line-strong ${
                        section.tone === "success"
                          ? "data-[tone=success]:bg-success-bg"
                          : ""
                      }`}
                      aria-expanded={!collapsedPlatforms.has(section.key)}
                      onClick={() => {
                        const next = new Set(collapsedPlatforms);
                        if (next.has(section.key)) next.delete(section.key);
                        else next.add(section.key);
                        setCollapsedPlatforms(next);
                      }}
                    >
                      <span className="flex items-center gap-2 font-semibold text-ink-strong">
                        <ChevronDown
                          size={13}
                          className={`transition-transform duration-[var(--motion-base)] ${
                            collapsedPlatforms.has(section.key)
                              ? "-rotate-90"
                              : "rotate-0"
                          }`}
                          aria-hidden
                        />
                        {section.label}
                      </span>
                      <span className="rounded-[var(--radius-pill)] border border-line bg-panel px-2 py-0.5 tabular text-[11.5px] font-medium text-ink-muted">
                        {section.sites.length} 个
                      </span>
                    </button>
                  </td>
                </tr>
              ) : null}
              {!groupByPlatform || !collapsedPlatforms.has(section.key)
                ? section.sites.map((site) => {
                    const authCount = Number(
                      site.current_login_groups_count || 0,
                    );
                    const publicCount = Number(site.current_groups_count || 0);
                    const hiddenCount = Math.max(0, authCount - publicCount);
                    const selected = site.id === selectedId;
                    return (
                      <tr
                        key={site.id}
                        className={`group border-b border-line-soft transition-colors duration-[var(--motion-fast)] last:border-0 hover:bg-sunken-hover ${
                          selected ? "bg-sunken" : ""
                        }`}
                      >
                        <td className="min-w-0 py-3 pr-3 align-middle">
                          <div className="flex items-center gap-3">
                            <span
                              className={`h-9 w-[2px] shrink-0 rounded-full transition-colors duration-[var(--motion-fast)] ${
                                selected
                                  ? "bg-accent"
                                  : "bg-transparent group-hover:bg-line-strong"
                              }`}
                              aria-hidden
                            />
                            <div className="min-w-0">
                              <div className="font-semibold text-ink-strong">
                                {site.name}
                              </div>
                              <div className="mt-0.5 truncate font-mono text-[11px] text-ink-soft">
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
                              <Badge tone="warning">{hiddenCount} 隐藏</Badge>
                            ) : (
                              <span className="text-[11.5px] text-ink-faint">
                                无
                              </span>
                            )}
                            {site.auth_mode === "browser" ? (
                              <Badge
                                tone={sessionSyncTone(site.session_sync_status)}
                              >
                                {sessionSyncLabel(site.session_sync_status)}
                              </Badge>
                            ) : site.platform === "sub2api" ? (
                              <Badge tone="info">用户登录</Badge>
                            ) : truthy(site.login_enabled) ? (
                              <Badge tone="info">认证增强</Badge>
                            ) : null}
                          </div>
                        </td>
                        <td className="py-3 pr-3 align-middle tabular text-ink-strong">
                          {site.current_groups_count || 0}
                        </td>
                        <td className="py-3 pr-3 align-middle text-[11.5px] text-ink-muted">
                          <TimeCell value={site.last_check_at} />
                        </td>
                        <td className="py-3 align-middle">
                          <div className="flex flex-nowrap items-center justify-end gap-1">
                            {site.auth_mode === "browser"
                              ? isSessionSyncRetryable(
                                  site.session_sync_status,
                                ) && (
                                  <Button
                                    variant="secondary"
                                    size="sm"
                                    className="shrink-0"
                                    aria-label="同步登录态"
                                    title="从浏览器同步登录态"
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
                                    {syncingId === site.id ? null : (
                                      <RefreshCw size={12} />
                                    )}
                                    同步
                                  </Button>
                                )
                              : (
                                  <Button
                                    variant="secondary"
                                    size="sm"
                                    className="shrink-0"
                                    aria-label="同步凭证"
                                    title={
                                      site.auth_mode === "token"
                                        ? "更新 Token"
                                        : "更新账号密码"
                                    }
                                    onClick={() => onEdit(site)}
                                  >
                                    <Pencil size={12} />
                                    同步
                                  </Button>
                                )}
                            <Button
                              variant="brand"
                              size="sm"
                              className="shrink-0"
                              aria-label="查看倍率"
                              title="查看倍率"
                              onClick={() => onRatios(site)}
                            >
                              <Gauge size={12} />
                              倍率
                            </Button>
                            <Button
                              variant="secondary"
                              size="sm"
                              className="shrink-0"
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
                              {checkingId === site.id ? null : (
                                <RefreshCw size={12} />
                              )}
                              检测
                            </Button>
                            <RowMoreMenu
                              onView={() => onView(site)}
                              onEdit={() => onEdit(site)}
                              onDelete={() => onDelete(site)}
                            />
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

function EmptySites() {
  return (
    <div className="flex flex-col items-center gap-1.5 py-10 text-center">
      <div className="font-serif text-[15px] font-semibold text-ink-strong">
        还没有渠道
      </div>
      <p className="max-w-sm text-[12.5px] leading-relaxed text-ink-muted">
        从右上角「添加渠道」开始，先接入一个 NewAPI / sub2api 上游站点，再决定抓取频率和认证方式。
      </p>
    </div>
  );
}

/** 「更多」菜单：把低频 / 危险操作（查看详情、编辑、删除）收进下拉，避免每行 6 个按钮挤占横向空间。 */
function RowMoreMenu({
  onView,
  onEdit,
  onDelete,
}: {
  onView: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        aria-label="更多操作"
        title="更多"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-7 w-7 items-center justify-center rounded-[var(--radius-sm)] border border-line bg-panel text-ink-muted transition-[border-color,color] duration-[var(--motion-fast)] hover:border-line-strong hover:text-ink-strong"
      >
        <MoreHorizontal size={14} />
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-8 z-20 min-w-[140px] overflow-hidden rounded-[var(--radius-md)] border border-line bg-panel py-1 shadow-[var(--shadow-floating)]"
        >
          <RowMenuItem
            icon={<Eye size={13} />}
            label="查看详情"
            onClick={() => {
              setOpen(false);
              onView();
            }}
          />
          <RowMenuItem
            icon={<Pencil size={13} />}
            label="编辑渠道"
            onClick={() => {
              setOpen(false);
              onEdit();
            }}
          />
          <div className="my-1 border-t border-line-soft" aria-hidden />
          <RowMenuItem
            icon={<Trash2 size={13} />}
            label="删除"
            danger
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
          />
        </div>
      ) : null}
    </div>
  );
}

function RowMenuItem({
  icon,
  label,
  onClick,
  danger,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] transition-colors duration-[var(--motion-fast)] ${
        danger
          ? "text-danger-fg hover:bg-danger-bg"
          : "text-ink hover:bg-sunken-hover"
      }`}
    >
      <span
        className={`shrink-0 ${danger ? "text-danger-fg" : "text-ink-muted"}`}
        aria-hidden
      >
        {icon}
      </span>
      {label}
    </button>
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
    <span className="whitespace-nowrap tabular leading-[1.35]">
      <span className="block">{date}</span>
      {time ? <span className="block text-ink-soft">{time}</span> : null}
    </span>
  );
}
