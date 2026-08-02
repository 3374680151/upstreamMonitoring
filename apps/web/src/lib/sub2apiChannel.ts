import type { Channel } from "./types";

const TOKENS_PER_MTOK = 1_000_000;

export function sub2ApiPerTokenToMTok(value: number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  return Number.parseFloat((value * TOKENS_PER_MTOK).toPrecision(10));
}

export function sub2ApiMTokToPerToken(value: number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  return Number.parseFloat((value / TOKENS_PER_MTOK).toPrecision(10));
}

export function isSub2ApiChannel(channel: Channel): boolean {
  return channel.source_platform === "sub2api";
}

export function normalizedChannelStatus(
  channel: Pick<Channel, "status" | "normalized_status">,
): "active" | "disabled" | "error" {
  if (channel.normalized_status === "active") return "active";
  if (channel.normalized_status === "disabled") return "disabled";
  if (channel.normalized_status === "error") return "error";
  if (channel.status === 1 || channel.status === "active") return "active";
  if (channel.status === 2 || channel.status === "disabled") return "disabled";
  return "error";
}

export function sub2ApiFeaturesText(value: Channel["features"]): string {
  if (Array.isArray(value)) return value.filter(Boolean).join(", ");
  return typeof value === "string" ? value : "";
}

export function editedSub2ApiFeatures(
  original: Channel["features"],
  value: string,
): string | string[] {
  if (!Array.isArray(original)) return value;
  return [
    ...new Set(value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean)),
  ];
}

const editableFields = [
  "name",
  "description",
  "status",
  "group_ids",
  "model_pricing",
  "model_mapping",
  "billing_model_source",
  "restrict_models",
  "features",
  "features_config",
  "apply_pricing_to_account_stats",
  "account_stats_pricing_rules",
] as const;

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return Object.fromEntries(
      Object.keys(record)
        .sort()
        .map((key) => [key, stableValue(record[key])]),
    );
  }
  return value;
}

function stableJson(value: unknown): string {
  return JSON.stringify(stableValue(value));
}

export function buildSub2ApiChannelPatch(
  original: Channel,
  edited: Channel,
): Partial<Channel> {
  const patch: Partial<Channel> = {};
  for (const field of editableFields) {
    if (stableJson(original[field]) !== stableJson(edited[field])) {
      (patch as Record<string, unknown>)[field] = edited[field];
    }
  }
  return patch;
}
