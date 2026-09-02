/** NewAPI pricing + perf-metrics helpers (mirror upstream frontend). */

import type { GroupItem, ModelHealth } from "./types";

export type PricingModel = {
  model_name: string;
  enable_groups?: string[];
  model_ratio?: number;
  model_price?: number;
  completion_ratio?: number;
  cache_ratio?: number;
  vendor_id?: number;
  vendor_name?: string;
  owner_by?: string;
  quota_type?: number;
  [key: string]: unknown;
};

export type PricingResponse = {
  success?: boolean;
  data?: PricingModel[];
  usable_group?: Record<string, unknown>;
  group_ratio?: Record<string, number>;
  vendors?: unknown[];
  auto_groups?: string[];
  message?: string;
  auth_used?: boolean;
};

export type PerfSummaryModel = {
  model_name: string;
  avg_latency_ms?: number;
  success_rate?: number;
  avg_tps?: number;
  recent_success_rates?: number[];
  request_count?: number;
};

export type PerfSummaryResponse = {
  success?: boolean;
  data?: { models?: PerfSummaryModel[] };
  hours?: number;
  message?: string;
  note?: string;
};

export type PerfSeriesPoint = {
  ts?: number;
  avg_ttft_ms?: number;
  avg_latency_ms?: number;
  success_rate?: number;
  avg_tps?: number;
};

export type PerfGroupRow = {
  group: string;
  avg_ttft_ms?: number;
  avg_latency_ms?: number;
  success_rate?: number;
  avg_tps?: number;
  series?: PerfSeriesPoint[];
};

export type PerfDetailResponse = {
  success?: boolean;
  data?: {
    model_name?: string;
    series_schema?: string;
    groups?: PerfGroupRow[];
  };
  hours?: number;
  message?: string;
};

export const PERF_HOUR_OPTIONS = [
  { value: 1 / 6, label: "最近 10 分钟" },
  { value: 1 / 3, label: "最近 20 分钟" },
  { value: 1, label: "最近 1 小时" },
] as const;

export const DEFAULT_PERF_HOURS = 1;

export function modelInGroup(model: PricingModel, group: string): boolean {
  if (!group || group === "all") return true;
  const groups = model.enable_groups || [];
  return groups.includes(group) || groups.includes("all");
}

export function filterModelsByGroup(
  models: PricingModel[],
  group: string,
): PricingModel[] {
  return models.filter((m) => modelInGroup(m, group));
}

export function modelVendorLabel(model: PricingModel): string {
  const owner = String(model.owner_by || model.vendor_name || "").trim();
  if (owner) return owner;
  if (model.vendor_id != null && String(model.vendor_id).trim()) {
    return `vendor:${model.vendor_id}`;
  }
  return "未标注厂商";
}

export function filterModelsByQuery(
  models: PricingModel[],
  query: string,
  vendor: string,
): PricingModel[] {
  const normalized = query.trim().toLocaleLowerCase();
  return models.filter((model) => {
    if (vendor && vendor !== "all" && modelVendorLabel(model) !== vendor) {
      return false;
    }
    if (!normalized) return true;
    return [model.model_name, modelVendorLabel(model), model.owner_by]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase().includes(normalized));
  });
}

export function buildPerfMap(
  summary: PerfSummaryResponse | null,
): Map<string, PerfSummaryModel> {
  const map = new Map<string, PerfSummaryModel>();
  for (const item of summary?.data?.models || []) {
    if (item?.model_name) map.set(item.model_name, item);
  }
  return map;
}

/** Global success-rate semantics: >80 green, 60-80 orange, <60 red. */
export function successTone(
  rate?: number | null,
): "success" | "warning" | "danger" | "neutral" {
  if (rate == null || !Number.isFinite(Number(rate))) return "neutral";
  const n = Number(rate);
  if (n > 80) return "success";
  if (n >= 60) return "warning";
  return "danger";
}

/** Prefer last 3 bucket rates average when present. */
export function effectiveSuccessRate(perf?: PerfSummaryModel | null): number | null {
  if (!perf) return null;
  const recent = (perf.recent_success_rates || []).filter((x) =>
    Number.isFinite(Number(x)),
  );
  if (recent.length) {
    return recent.reduce((a, b) => a + Number(b), 0) / recent.length;
  }
  if (perf.success_rate != null && Number.isFinite(Number(perf.success_rate))) {
    return Number(perf.success_rate);
  }
  return null;
}

export function formatMs(value?: number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return "-";
  const n = Number(value);
  if (n >= 10000) return `${(n / 1000).toFixed(1)}s`;
  return `${Math.round(n)}ms`;
}

export function formatRate(value?: number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return "-";
  return `${Number(value).toFixed(2)}%`;
}

export function formatTps(value?: number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return "-";
  return Number(value).toFixed(1);
}

export function usableGroupKeys(
  usable?: Record<string, unknown> | null,
  groupRatio?: Record<string, number> | null,
): string[] {
  const keys = new Set<string>();
  Object.keys(usable || {}).forEach((k) => keys.add(k));
  Object.keys(groupRatio || {}).forEach((k) => keys.add(k));
  return Array.from(keys).sort((a, b) => a.localeCompare(b, "zh-CN"));
}

export function effectiveRatio(
  model: PricingModel,
  group: string,
  groupRatio?: Record<string, number> | null,
): number | null {
  const mr = Number(model.model_ratio);
  if (!Number.isFinite(mr)) return null;
  if (!group || group === "all") return mr;
  const gr = Number(groupRatio?.[group]);
  if (!Number.isFinite(gr)) return mr;
  return mr * gr;
}

// ---------------------------------------------------------------------------
// Shared group-catalog helpers (used by UpstreamGroupsPopover + RatiosDialog).
// ---------------------------------------------------------------------------

export type GroupSummary = {
  modelCount: number;
  monitoredCount: number;
  successRate: number | null;
  avgLatencyMs: number | null;
  avgTps: number | null;
  sampleCount: number | null;
};

/** Mean of finite numeric values; null when nothing is measurable. */
export function averageNumbers(
  values: Array<number | undefined>,
): number | null {
  const valid = values
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value));
  return valid.length
    ? valid.reduce((sum, value) => sum + value, 0) / valid.length
    : null;
}

/**
 * Catalog order shared by the hover popover and the ratios dialog:
 * gpt/claude groups first, then ratio (non-finite last), then name.
 */
export function compareGroupEntries(
  [aName, aItem]: [string, GroupItem],
  [bName, bItem]: [string, GroupItem],
): number {
  const priority = (name: string) => (/gpt|claude|clade/i.test(name) ? 0 : 1);
  const aPriority = priority(aName);
  const bPriority = priority(bName);
  if (aPriority !== bPriority) return aPriority - bPriority;
  const ratioOf = (item: GroupItem | undefined) => {
    const value = Number(item?.ratio);
    return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
  };
  const aRatio = ratioOf(aItem);
  const bRatio = ratioOf(bItem);
  if (aRatio !== bRatio) return aRatio - bRatio;
  return aName.localeCompare(bName, "zh-CN");
}

export function summarizeNewApiGroup(
  groupName: string,
  pricing: PricingResponse | null,
  perfMap: Map<string, PerfSummaryModel>,
): GroupSummary {
  const list = (pricing?.data || []).filter((model) =>
    modelInGroup(model, groupName),
  );
  const perfs = list
    .map((model) => perfMap.get(model.model_name))
    .filter((perf): perf is PerfSummaryModel => Boolean(perf));
  const samples = perfs
    .map((perf) => Number(perf.request_count))
    .filter((value) => Number.isFinite(value));
  return {
    modelCount: list.length,
    monitoredCount: perfs.length,
    successRate: averageNumbers(perfs.map((perf) => perf.success_rate)),
    avgLatencyMs: averageNumbers(perfs.map((perf) => perf.avg_latency_ms)),
    avgTps: averageNumbers(perfs.map((perf) => perf.avg_tps)),
    sampleCount: samples.length
      ? samples.reduce((sum, value) => sum + value, 0)
      : null,
  };
}

export function summarizeSub2ApiGroup(
  groupName: string,
  models: Record<string, ModelHealth[]> | null,
): GroupSummary {
  const list = models?.[groupName] || [];
  return {
    modelCount: list.length,
    monitoredCount: list.filter(
      (model) => model.status && model.status !== "configured",
    ).length,
    successRate: averageNumbers(
      list.map((model) => model.availability_7d ?? undefined),
    ),
    avgLatencyMs: averageNumbers(
      list.map((model) => model.latency_ms ?? model.ping_latency_ms ?? undefined),
    ),
    avgTps: null,
    sampleCount: null,
  };
}
