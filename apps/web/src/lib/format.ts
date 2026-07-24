import type { Change, GroupItem, Site } from "./types";

export function fmtTime(value?: string | null): string {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", { hour12: false });
}

export function platformLabel(siteOrValue?: Site | string | null): string {
  const value =
    typeof siteOrValue === "string"
      ? siteOrValue
      : siteOrValue?.platform || "newapi";
  return value === "sub2api" ? "sub2api" : "NewAPI";
}

export function statusTone(
  status?: string,
): "success" | "warning" | "danger" | "neutral" | "info" {
  if (status === "ok") return "success";
  if (status === "failed") return "danger";
  if (status === "warning") return "warning";
  return "neutral";
}

export function changeTone(
  change: Change,
): "success" | "warning" | "danger" | "neutral" {
  if (change.change_type === "group_removed") return "danger";
  if (
    change.change_type === "ratio_changed" &&
    Number(change.change_percent) > 0
  ) {
    return "warning";
  }
  if (change.change_type === "ratio_changed") return "success";
  return "neutral";
}

export function changeTypeLabel(type?: string | null): string {
  const labels: Record<string, string> = {
    ratio_changed: "倍率变化",
    group_added: "新增分组",
    group_removed: "删除分组",
    desc_changed: "描述变化",
    status_changed: "状态变化",
    is_exclusive_changed: "专属变化",
    subscription_type_changed: "订阅变化",
    rpm_limit_changed: "RPM 变化",
    platform_changed: "平台变化",
  };
  return labels[type || ""] || type || "-";
}

export function ratioLabel(item?: GroupItem | { ratio?: unknown; ratio_type?: string } | null): string {
  if (!item) return "-";
  const ratio = item.ratio;
  if (item.ratio_type === "text") return `${ratio}`;
  const n = Number(ratio);
  if (Number.isFinite(n)) return `${n.toFixed(2)}x`;
  return ratio == null ? "-" : `${ratio}`;
}

export function groupPropertyText(item: GroupItem = {}): string {
  const values = [
    item.platform,
    item.status,
    item.is_exclusive ? "专属" : "",
    item.subscription_type,
    item.rpm_limit === undefined ||
    item.rpm_limit === null ||
    item.rpm_limit === ""
      ? ""
      : `RPM ${item.rpm_limit}`,
    item.desc,
  ].filter(Boolean);
  return values.join(" · ") || "-";
}

export function modelStatusLabel(status?: string): string {
  const labels: Record<string, string> = {
    operational: "正常",
    degraded: "降级",
    failed: "失败",
    error: "异常",
    active: "启用",
    enabled: "启用",
    inactive: "停用",
    disabled: "停用",
    configured: "已配置",
    maintenance: "维护中",
  };
  return labels[String(status || "").toLowerCase()] || status || "";
}

export function modelStatusTone(
  status?: string,
): "success" | "warning" | "danger" | "neutral" {
  const normalized = String(status || "").toLowerCase();
  if (["operational", "active", "enabled"].includes(normalized)) return "success";
  if (["degraded", "maintenance"].includes(normalized)) return "warning";
  if (["failed", "error", "inactive", "disabled"].includes(normalized)) {
    return "danger";
  }
  return "neutral";
}

export function modelMetricText(
  value?: number | null,
  suffix = "ms",
): string {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return "-";
  }
  return `${Number(value).toLocaleString("zh-CN")} ${suffix}`;
}

export function siteNameById(sites: Site[], siteId: number): string {
  return sites.find((s) => s.id === siteId)?.name || `#${siteId}`;
}

export function truthy(value: unknown): boolean {
  return value === true || value === 1 || value === "1";
}
