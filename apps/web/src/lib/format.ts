import type { Change, GroupItem, Site } from "./types";

const ratioNumberFormat = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 6,
  useGrouping: false,
});

export function fmtTime(value?: string | null): string {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function fmtTimeParts(value?: string | null): [string, string] {
  const formatted = fmtTime(value);
  const separator = formatted.indexOf(" ");
  return separator === -1
    ? [formatted, ""]
    : [formatted.slice(0, separator), formatted.slice(separator + 1)];
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

export function statusLabel(status?: string | null): string {
  const labels: Record<string, string> = {
    ok: "正常",
    warning: "警告",
    failed: "异常",
    unknown: "未知",
  };
  return labels[String(status || "").toLowerCase()] || status || "未知";
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
  if (change.change_type === "model_removed_from_group") return "warning";
  if (change.change_type === "model_added_to_group") return "success";
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
    model_added_to_group: "模型上架",
    model_removed_from_group: "模型下架",
  };
  return labels[type || ""] || type || "-";
}

export function changeDisplayMessage(change: Change): string {
  const message =
    change.message ||
    (change.change_type === "group_added"
      ? `新增分组 ${change.group_name || "-"}`
      : "-");
  if (change.change_type !== "group_added" || message.includes("· 倍率 ")) {
    return message;
  }

  let value: unknown = change.new_value;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return message;
    }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return message;
  }

  const ratio = ratioLabel(value as GroupItem);
  return ratio === "-" ? message : `${message} · 倍率 ${ratio}`;
}

export function ratioLabel(item?: GroupItem | { ratio?: unknown; ratio_type?: string } | null): string {
  if (!item) return "-";
  const ratio = item.ratio;
  if (item.ratio_type === "text") return `${ratio}`;
  const n = Number(ratio);
  if (Number.isFinite(n)) return `${ratioNumberFormat.format(n)}x`;
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
