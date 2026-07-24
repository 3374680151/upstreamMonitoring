import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import {
  fmtTime,
  groupPropertyText,
  modelMetricText,
  modelStatusLabel,
  modelStatusTone,
  platformLabel,
  ratioLabel,
} from "@/lib/format";
import type { ModelHealth, Site } from "@/lib/types";
import { Badge } from "./Badge";
import { Modal } from "./ui";

export function RatiosDialog({
  site,
  open,
  onClose,
}: {
  site: Site | null;
  open: boolean;
  onClose: () => void;
}) {
  const [models, setModels] = useState<Record<string, ModelHealth[]> | null>(null);
  const [error, setError] = useState("");
  const [fetchedAt, setFetchedAt] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!open || !site) return;
    let cancelled = false;
    setModels(null);
    setError("");
    setFetchedAt("");
    setExpanded(new Set());
    api
      .siteModels(site.id)
      .then((resp) => {
        if (cancelled) return;
        setModels(resp.models_by_group || {});
        setFetchedAt(resp.fetched_at || "");
      })
      .catch((err) => {
        if (cancelled) return;
        setModels({});
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [open, site?.id]);

  const groups = useMemo(() => {
    if (!site) return [] as Array<[string, any]>;
    const publicGroups = site.current_groups || {};
    const loginGroups = site.current_login_groups || {};
    const hasAuth = Boolean(site.login_enabled && Object.keys(loginGroups).length);
    return Object.entries(hasAuth ? loginGroups : publicGroups).sort(([a], [b]) =>
      a.localeCompare(b, "zh-CN"),
    );
  }, [site]);

  if (!site) return null;

  const hasAuth = Boolean(
    site.login_enabled && Object.keys(site.current_login_groups || {}).length,
  );
  const source =
    site.platform === "sub2api"
      ? "用户可见分组"
      : hasAuth
        ? "认证可见分组"
        : "公开分组";

  return (
    <Modal
      open={open}
      wide
      title={`${site.name} 分组倍率`}
      subtitle={`${platformLabel(site)} · ${site.base_url}`}
      onClose={onClose}
    >
      <div className="mb-4 text-xs text-[var(--color-text-muted)]">
        {source} · {groups.length} 个分组 · 上次检测 {fmtTime(site.last_check_at)}
        {models === null
          ? " · 正在读取上游模型"
          : error
            ? " · 模型读取失败"
            : ` · 模型读取 ${fmtTime(fetchedAt)}`}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border-subtle)] text-xs font-semibold text-[var(--color-text-muted)]">
              <th className="pb-2">分组</th>
              <th className="pb-2">倍率</th>
              <th className="pb-2">模型状态 / 倍率</th>
              <th className="pb-2">属性</th>
            </tr>
          </thead>
          <tbody>
            {groups.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-8 text-center text-[var(--color-text-muted)]">
                  暂无倍率数据
                </td>
              </tr>
            ) : (
              groups.map(([name, item]) => (
                <tr
                  key={name}
                  className="border-b border-[var(--color-border-subtle)] align-top last:border-0"
                >
                  <td className="py-3 pr-3 font-bold text-[var(--color-text-primary)]">
                    {name}
                  </td>
                  <td className="py-3 pr-3 font-extrabold tabular-nums text-[var(--color-text-primary)]">
                    {ratioLabel(item)}
                  </td>
                  <td className="py-3 pr-3">
                    <ModelCell
                      siteId={site.id}
                      groupName={name}
                      models={models}
                      error={error}
                      expanded={expanded}
                      setExpanded={setExpanded}
                    />
                  </td>
                  <td className="py-3 text-xs text-[var(--color-text-muted)]">
                    {groupPropertyText(item || {})}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {error ? (
        <div className="mt-3 rounded-xl bg-[var(--color-danger-bg)] px-3 py-2 text-xs text-[var(--color-danger-text)]">
          {error}
        </div>
      ) : null}
    </Modal>
  );
}

function ModelCell({
  siteId,
  groupName,
  models,
  error,
  expanded,
  setExpanded,
}: {
  siteId: number;
  groupName: string;
  models: Record<string, ModelHealth[]> | null;
  error: string;
  expanded: Set<string>;
  setExpanded: (s: Set<string>) => void;
}) {
  if (models === null) {
    return <span className="text-xs text-[var(--color-text-soft)]">正在读取上游模型...</span>;
  }
  if (error) {
    return <span className="text-xs text-[var(--color-danger-text)]">{error}</span>;
  }
  const list = models[groupName] || [];
  if (!list.length) {
    return (
      <span className="text-xs text-[var(--color-text-soft)]">
        上游未返回该分组的模型数据
      </span>
    );
  }
  return (
    <div className="space-y-2">
      {list.map((model, index) => {
        const key = `${siteId}|${groupName}|${model.name}|${index}`;
        const open = expanded.has(key);
        const tone = modelStatusTone(model.status);
        const hasAvail =
          model.availability_7d !== null &&
          model.availability_7d !== undefined &&
          Number.isFinite(Number(model.availability_7d));
        const availability = hasAvail
          ? `${Number(model.availability_7d).toFixed(1)}%`
          : model.status === "configured"
            ? "未公开"
            : "-";
        return (
          <div
            key={key}
            className="rounded-xl border border-[var(--color-border)] bg-[var(--color-panel-soft)]"
          >
            <button
              type="button"
              className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
              onClick={() => {
                const next = new Set(expanded);
                if (next.has(key)) next.delete(key);
                else next.add(key);
                setExpanded(next);
              }}
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-[var(--color-text-primary)]">
                  {model.name}
                </div>
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                <Badge tone={tone}>{modelStatusLabel(model.status) || "未监控"}</Badge>
                <span className="text-[11px] text-[var(--color-text-muted)]">
                  可用 {availability}
                </span>
                <span className="text-[11px] font-bold tabular-nums text-[var(--color-text-primary)]">
                  {ratioLabel(model)}
                </span>
              </div>
            </button>
            {open ? (
              <div className="space-y-2 border-t border-[var(--color-border-subtle)] px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
                <div>
                  {[model.source, model.monitor, model.platform].filter(Boolean).join(" · ") ||
                    "-"}
                </div>
                {model.status && model.status !== "configured" ? (
                  <div className="flex gap-4">
                    <span>
                      延迟 <b className="text-[var(--color-text-primary)]">{modelMetricText(model.latency_ms)}</b>
                    </span>
                    <span>
                      PING <b className="text-[var(--color-text-primary)]">{modelMetricText(model.ping_latency_ms)}</b>
                    </span>
                  </div>
                ) : (
                  <div>上游已返回模型配置，但未公开健康监控数据</div>
                )}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
