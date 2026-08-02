import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import {
  DEFAULT_PERF_HOURS,
  PERF_HOUR_OPTIONS,
  buildPerfMap,
  effectiveSuccessRate,
  formatMs,
  formatRate,
  formatTps,
  modelInGroup,
  successTone,
  type PerfSummaryModel,
  type PricingModel,
  type PricingResponse,
} from "@/lib/perf";
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
import { Button, Modal, Select } from "./ui";

function groupPriority(name: string): number {
  return /gpt|claude|clade/i.test(name) ? 0 : 1;
}

function numericRatio(item: { ratio?: number | string } | null | undefined): number {
  const value = Number(item?.ratio);
  return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
}

function siteGroupNames(site: Site): string[] {
  const publicGroups = site.current_groups || {};
  const loginGroups = site.current_login_groups || {};
  const hasAuth = Boolean(site.login_enabled && Object.keys(loginGroups).length);
  return Object.keys(hasAuth ? loginGroups : publicGroups);
}

type GroupSummary = {
  modelCount: number;
  monitoredCount: number;
  successRate: number | null;
  avgLatencyMs: number | null;
  avgTps: number | null;
  sampleCount: number | null;
};

function averageNumbers(values: Array<number | undefined>): number | null {
  const valid = values
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value));
  return valid.length
    ? valid.reduce((sum, value) => sum + value, 0) / valid.length
    : null;
}

function summarizeNewApiGroup(
  groupName: string,
  pricing: PricingResponse | null,
  perfMap: Map<string, PerfSummaryModel>,
): GroupSummary {
  const models = (pricing?.data || []).filter((model) =>
    modelInGroup(model, groupName),
  );
  const perfs = models
    .map((model) => perfMap.get(model.model_name))
    .filter((perf): perf is PerfSummaryModel => Boolean(perf));
  const samples = perfs
    .map((perf) => Number(perf.request_count))
    .filter((value) => Number.isFinite(value));
  return {
    modelCount: models.length,
    monitoredCount: perfs.length,
    successRate: averageNumbers(perfs.map((perf) => perf.success_rate)),
    avgLatencyMs: averageNumbers(perfs.map((perf) => perf.avg_latency_ms)),
    avgTps: averageNumbers(perfs.map((perf) => perf.avg_tps)),
    sampleCount: samples.length
      ? samples.reduce((sum, value) => sum + value, 0)
      : null,
  };
}

function summarizeLegacyGroup(
  groupName: string,
  models: Record<string, ModelHealth[]> | null,
): GroupSummary {
  const list = models?.[groupName] || [];
  return {
    modelCount: list.length,
    monitoredCount: list.filter((model) => model.status && model.status !== "configured").length,
    successRate: averageNumbers(list.map((model) => model.availability_7d ?? undefined)),
    avgLatencyMs: averageNumbers(
      list.map((model) => model.latency_ms ?? model.ping_latency_ms ?? undefined),
    ),
    avgTps: null,
    sampleCount: null,
  };
}

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
  const [pricing, setPricing] = useState<PricingResponse | null>(null);
  const [summaryModels, setSummaryModels] = useState<PerfSummaryModel[]>([]);
  const [error, setError] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const [perfError, setPerfError] = useState("");
  const [perfLoading, setPerfLoading] = useState(false);
  const [perfHours, setPerfHours] = useState(DEFAULT_PERF_HOURS);
  const [fetchedAt, setFetchedAt] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedGroup, setSelectedGroup] = useState("all");
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!open || !site) {
      if (!open) setPerfLoading(false);
      return;
    }
    let cancelled = false;
    setModels(null);
    setPricing(null);
    setSummaryModels([]);
    setError("");
    setCatalogError("");
    setPerfError("");
    setFetchedAt("");
    setPerfLoading(site.platform === "newapi");

    if (site.platform === "newapi") {
      Promise.allSettled([
        api.sitePricing(site.id),
        api.sitePerfSummary(site.id, perfHours),
      ])
        .then(([pricingResult, summaryResult]) => {
          if (cancelled) return;
          const issues: string[] = [];
          if (pricingResult.status === "fulfilled") {
            setPricing(pricingResult.value);
            setFetchedAt(new Date().toISOString());
          } else {
            const message = errorText(pricingResult.reason);
            setCatalogError(message);
            issues.push(`模型清单：${message}`);
          }
          if (summaryResult.status === "fulfilled") {
            setSummaryModels(summaryResult.value.data?.models || []);
          } else {
            const message = errorText(summaryResult.reason);
            setPerfError(message);
            issues.push(`模型状态：${message}`);
          }
          setError(issues.join("；"));
        })
        .finally(() => {
          if (!cancelled) setPerfLoading(false);
        });
      return () => {
        cancelled = true;
      };
    }

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
        const message = errorText(err);
        setCatalogError(message);
        setError(message);
      });
    return () => {
      cancelled = true;
    };
  }, [open, site?.id, perfHours]);

  useEffect(() => {
    if (!open) return;
    setSelectedGroup("all");
    setExpanded(new Set());
    setCollapsedGroups(site ? new Set(siteGroupNames(site)) : new Set());
  }, [open, site?.id]);

  const groups = useMemo(() => {
    if (!site) return [] as Array<[string, any]>;
    const publicGroups = site.current_groups || {};
    const loginGroups = site.current_login_groups || {};
    const hasAuth = Boolean(site.login_enabled && Object.keys(loginGroups).length);
    return Object.entries(hasAuth ? loginGroups : publicGroups).sort(
      ([a, aItem], [b, bItem]) => {
        const aPriority = groupPriority(a);
        const bPriority = groupPriority(b);
        if (aPriority !== bPriority) return aPriority - bPriority;
        const aRatio = numericRatio(aItem);
        const bRatio = numericRatio(bItem);
        if (aRatio !== bRatio) return aRatio - bRatio;
        return a.localeCompare(b, "zh-CN");
      },
    );
  }, [site]);

  const isNewApi = site?.platform === "newapi";
  const perfMap = useMemo(
    () => buildPerfMap({ data: { models: summaryModels } }),
    [summaryModels],
  );
  const selectedHoursLabel =
    PERF_HOUR_OPTIONS.find((option) => option.value === perfHours)?.label ||
    `${perfHours} 小时`;
  const visibleGroups = useMemo(
    () =>
      selectedGroup === "all"
        ? groups
        : groups.filter(([name]) => name === selectedGroup),
    [groups, selectedGroup],
  );

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
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="text-xs text-[var(--color-text-muted)]">
          {source} · {groups.length} 个分组 · 上次检测 {fmtTime(site.last_check_at)}
          {isNewApi
            ? ` · 模型清单 ${pricing ? "已读取" : "读取中"} · 状态范围 ${selectedHoursLabel}`
            : models === null
              ? " · 正在读取上游模型"
              : error
                ? " · 模型读取失败"
                : ` · 模型读取 ${fmtTime(fetchedAt)}`}
        </div>
        {isNewApi ? (
          <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            <span>状态时间</span>
            <Select
              className="w-28 py-1 text-xs"
              value={String(perfHours)}
              onChange={(event) => setPerfHours(Number(event.target.value))}
            >
              {PERF_HOUR_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </label>
        ) : null}
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            className="px-2 py-1 text-xs"
            onClick={() => setCollapsedGroups(new Set(visibleGroups.map(([name]) => name)))}
          >
            全部折叠
          </Button>
          <Button
            variant="ghost"
            className="px-2 py-1 text-xs"
            onClick={() => setCollapsedGroups(new Set())}
          >
            全部展开
          </Button>
        </div>
        <label className="flex items-center gap-1.5 text-xs font-semibold text-[var(--color-text-primary)]">
          <span>分组</span>
          <Select
            className="w-32 border-[var(--color-brand)] bg-[var(--color-surface)] py-1 text-xs font-semibold"
            value={selectedGroup}
            onChange={(event) => setSelectedGroup(event.target.value)}
          >
            <option value="all">全部分组</option>
            {groups.map(([name]) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
        </label>
      </div>

      {isNewApi ? (
        <div className="mb-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-info-bg)] px-3 py-2 text-xs text-[var(--color-info-text)]">
          模型清单按当前分组归属展示；状态来自上游 <code>perf-metrics/summary</code>，是模型级汇总，不代表单独某个分组的状态。
          分组默认收起，点击分组左侧“展开”查看模型。
          {summaryModels.length ? ` 已获取 ${summaryModels.length} 个模型的状态。` : ""}
        </div>
      ) : null}

      <div className="priceai-scrollbar overflow-x-auto pb-1">
        <table className="w-full min-w-max table-auto text-left text-sm">
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
              visibleGroups.map(([name, item]) => {
                const collapsed = collapsedGroups.has(name);
                const groupSummary = isNewApi
                  ? summarizeNewApiGroup(name, pricing, perfMap)
                  : summarizeLegacyGroup(name, models);
                return (
                <tr
                  key={name}
                  className="border-b border-[var(--color-border-subtle)] align-top last:border-0"
                >
                  <td className="py-3 pr-3 font-bold text-[var(--color-text-primary)]">
                    <button
                      type="button"
                      className="flex items-center gap-2 rounded-lg px-1.5 py-1 text-left hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-brand)]"
                      onClick={() => {
                        const next = new Set(collapsedGroups);
                        if (next.has(name)) next.delete(name);
                        else next.add(name);
                        setCollapsedGroups(next);
                      }}
                      aria-expanded={!collapsed}
                    >
                      <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-md bg-[var(--color-surface)] px-1 text-[10px] font-semibold text-[var(--color-text-muted)]">
                        {collapsed ? "展开" : "收起"}
                      </span>
                      <span>{name}</span>
                    </button>
                  </td>
                  <td className="py-3 pr-3 font-extrabold tabular-nums text-[var(--color-text-primary)]">
                    {ratioLabel(item)}
                  </td>
                  <td className="py-3 pr-3">
                    <GroupSummaryBar summary={groupSummary} collapsed={collapsed} />
                    {collapsed ? null : (
                      <ModelCell
                        siteId={site.id}
                        groupName={name}
                        models={models}
                        pricing={pricing}
                        perfMap={perfMap}
                        isNewApi={isNewApi}
                        perfLoading={perfLoading}
                        error={isNewApi ? catalogError : error}
                        expanded={expanded}
                        setExpanded={setExpanded}
                      />
                    )}
                  </td>
                  <td className="py-3 text-xs text-[var(--color-text-muted)]">
                    {groupPropertyText(item || {})}
                  </td>
                </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      {error || perfError ? (
        <div className="mt-3 rounded-xl bg-[var(--color-danger-bg)] px-3 py-2 text-xs text-[var(--color-danger-text)]">
          {error || `模型状态：${perfError}`}
        </div>
      ) : null}
    </Modal>
  );
}

function GroupSummaryBar({
  summary,
  collapsed,
}: {
  summary: GroupSummary;
  collapsed: boolean;
}) {
  const tone = successTone(summary.successRate);
  return (
    <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-[var(--color-border-subtle)] pb-2 text-[11px] text-[var(--color-text-muted)]">
      <span className="font-semibold text-[var(--color-text-primary)]">分组平均</span>
      <Badge tone={tone}>
        {summary.successRate == null
          ? "暂无成功率"
          : `成功率 ${formatRate(summary.successRate)}`}
      </Badge>
      <span className="tabular-nums">延迟 {formatMs(summary.avgLatencyMs)}</span>
      <span className="tabular-nums">TPS {formatTps(summary.avgTps)}</span>
      <span className="tabular-nums">
        样本 {summary.sampleCount == null ? "-" : summary.sampleCount}
      </span>
      <span className="tabular-nums">
        模型 {summary.monitoredCount}/{summary.modelCount}
      </span>
      {collapsed ? (
        <span className="text-[var(--color-text-soft)]">已折叠 · 点击展开</span>
      ) : null}
    </div>
  );
}

function ModelCell({
  siteId,
  groupName,
  models,
  pricing,
  perfMap,
  isNewApi,
  perfLoading,
  error,
  expanded,
  setExpanded,
}: {
  siteId: number;
  groupName: string;
  models: Record<string, ModelHealth[]> | null;
  pricing: PricingResponse | null;
  perfMap: Map<string, PerfSummaryModel>;
  isNewApi: boolean;
  perfLoading: boolean;
  error: string;
  expanded: Set<string>;
  setExpanded: (s: Set<string>) => void;
}) {
  if (isNewApi) {
    if (!pricing) {
      return (
        <span className="text-xs text-[var(--color-text-soft)]">
          {error || "正在读取上游模型清单..."}
        </span>
      );
    }
    const list = (pricing.data || []).filter((model) =>
      modelInGroup(model, groupName),
    );
    if (!list.length) {
      return (
        <span className="text-xs text-[var(--color-text-soft)]">
          上游未返回该分组的模型数据
        </span>
      );
    }
    return (
      <div className="space-y-2">
        {list.map((model) => (
          <NewApiModelRow
            key={`${groupName}|${model.model_name}`}
            model={model}
            perf={perfMap.get(model.model_name)}
            perfLoading={perfLoading}
          />
        ))}
      </div>
    );
  }

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
                <Badge tone={successTone(hasAvail ? Number(model.availability_7d) : null)}>
                  可用 {availability}
                </Badge>
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

function NewApiModelRow({
  model,
  perf,
  perfLoading,
}: {
  model: PricingModel;
  perf?: PerfSummaryModel;
  perfLoading: boolean;
}) {
  const recentRate = effectiveSuccessRate(perf);
  const tone = successTone(recentRate);
  const status = perfLoading
    ? "读取中"
    : recentRate == null
      ? "无样本"
      : tone === "success"
        ? "正常"
        : tone === "warning"
          ? "需关注"
          : "异常";
  const modelRatio = Number(model.model_ratio);
  const ratio = Number.isFinite(modelRatio) ? `${modelRatio.toFixed(2)}x` : "-";
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-panel-soft)] px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div className="min-w-0 truncate text-sm font-semibold text-[var(--color-text-primary)]">
          {model.model_name}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-x-2 gap-y-1 text-[11px]">
          <Badge tone={perfLoading ? "neutral" : tone}>{status}</Badge>
          <Badge tone={perfLoading ? "neutral" : tone} className="tabular-nums">
            成功率 {recentRate == null ? "-" : formatRate(recentRate)}
          </Badge>
          <span className="tabular-nums text-[var(--color-text-muted)]">
            延迟 {formatMs(perf?.avg_latency_ms)}
          </span>
          <span className="tabular-nums text-[var(--color-text-muted)]">
            TPS {formatTps(perf?.avg_tps)}
          </span>
          <span className="tabular-nums text-[var(--color-text-primary)]">
            {ratio}
          </span>
        </div>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-3 text-[10px] text-[var(--color-text-soft)]">
        <span>样本 {perf?.request_count == null ? "-" : perf.request_count}</span>
        {perf?.recent_success_rates?.length ? (
          <PerfBars values={perf.recent_success_rates} />
        ) : (
          <span>暂无最近时间桶</span>
        )}
        {recentRate != null ? (
          <span className="tabular-nums">近桶均值 {formatRate(recentRate)}</span>
        ) : null}
      </div>
    </div>
  );
}

function PerfBars({ values }: { values: number[] }) {
  const visibleValues = values.slice(-12);
  return (
    <span
      className="inline-flex h-4 items-end gap-0.5"
      title="最近时间桶成功率"
      role="img"
      aria-label={`最近时间桶成功率：${visibleValues.map(formatRate).join("、")}`}
    >
      {visibleValues.map((value, index) => {
        const number = Number(value);
        const height = Math.max(3, Math.min(16, (number / 100) * 16));
        const tone = successTone(number);
        const color =
          tone === "success"
            ? "bg-[var(--color-success-text)]"
            : tone === "warning"
              ? "bg-[var(--color-warning-text)]"
              : tone === "danger"
                ? "bg-[var(--color-danger-text)]"
                : "bg-[var(--color-surface-muted)]";
        return (
          <span
            key={index}
            className={`w-1.5 rounded-sm ${color}`}
            style={{ height }}
            title={`${formatRate(number)}`}
            aria-hidden="true"
          />
        );
      })}
    </span>
  );
}

function errorText(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}
