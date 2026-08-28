/**
 * 前端类型契约 — 与后端 `backend/api/schemas/*` 对应。
 * 后端 schemas 为 extra="allow" 透传，故多数类型保留 [key: string]: unknown
 * 以容纳上游平台字段，避免把宽松的透传契约收窄成编译错误。
 *
 * 资源域分组（同 `lib/api/*` 模块划分）：
 *   通用 / Platform / AuthMode / SessionSync*
 *   监控站点 / GroupItem / Site / Change / Overview / SiteAccount* / SiteCheck*
 *   快照 / SiteSnapshot
 *   渠道发现 / ChannelDiscovery* / SiteDiscoveryLink
 *   主站与渠道 / AdminSite* / Channel* / ChannelUpstreamBinding*
 *   推送 / NotificationSettings / NotificationLog
 */
export type Platform = "newapi" | "sub2api";
export type AuthMode = "password" | "token" | "browser";
export type SessionSyncTargetKind = "site" | "admin_site";
export type SessionSyncStatus =
  | "not_requested"
  | "pending"
  | "validating"
  | "ready"
  | "no_session"
  | "expired"
  | "permission_required"
  | "extension_unavailable"
  | "failed";
export type SiteStatus = "ok" | "warning" | "failed" | "unknown" | string;

export type GroupItem = {
  id?: number;
  name?: string;
  ratio?: number | string;
  rate_multiplier?: number | null;
  ratio_type?: string;
  desc?: string;
  platform?: string;
  status?: string;
  is_exclusive?: boolean;
  subscription_type?: string;
  rpm_limit?: number | string | null;
  [key: string]: unknown;
};

export type Site = {
  id: number;
  name: string;
  base_url: string;
  platform: Platform | string;
  enabled: boolean | number;
  interval_minutes: number;
  status: SiteStatus;
  last_error?: string | null;
  last_check_at?: string | null;
  next_check_at?: string | null;
  consecutive_failures?: number;
  current_groups_count?: number;
  current_login_groups_count?: number;
  current_groups?: Record<string, GroupItem>;
  current_login_groups?: Record<string, GroupItem>;
  login_enabled?: boolean | number;
  auth_mode?: AuthMode | string;
  login_username?: string | null;
  access_user_id?: string | null;
  has_access_token?: boolean;
  has_login_password?: boolean;
  platform_label?: string;
  has_refresh_token?: boolean;
  token_expires_at?: string | null;
  login_last_error?: string | null;
  login_last_check_at?: string | null;
  session_sync_status?: SessionSyncStatus;
  session_sync_error?: string | null;
  session_synced_at?: string | null;
  /** 最近一次检测失败原因：auth_expired / waf / network；null = 上次检测正常 */
  session_failure_kind?: string | null;
  has_browser_session?: boolean;
  /** 站点是否具备可用的用户认证能力（与后端匹配判定同一口径） */
  auth_ready?: boolean;
  /** 兜底系统访问令牌（服务端生成，仅回传脱敏布尔） */
  has_system_access_token?: boolean;
  system_token_fallback_enabled?: boolean | number;
};

export type ChannelDiscoveryCandidate = {
  base_url: string;
  name: string;
  channel_ids: number[];
  channel_names: string[];
  channel_count: number;
  existing_site_id?: number | null;
  existing_site_status?: string | null;
  /** 已存在站点的非敏感认证模式，用于决定是否允许发现面板重试浏览器同步。 */
  existing_site_auth_mode?: AuthMode | string | null;
  existing_site_enabled?: boolean | number | null;
  existing_site_session_sync_status?: SessionSyncStatus | string | null;
  importable?: boolean;
};

export type ChannelDiscoveryImportItem = {
  base_url: string;
  name?: string;
  channel_ids?: number[];
  channel_names?: string[];
};

export type SiteDiscoveryLink = {
  site_id: number;
  admin_site_id: number;
  admin_site_name: string;
  channel_id: number;
  channel_name?: string | null;
  upstream_base_url: string;
  created_at: string;
  updated_at: string;
};

export type ChannelDiscoveryImportResult = {
  base_url: string;
  name?: string;
  site_id?: number | null;
  status: "created" | "existing" | "invalid" | "conflict" | "failed" | string;
  message?: string | null;
};

export type SiteSessionSyncRequest = {
  request_id: string;
  secret: string;
  target_kind: SessionSyncTargetKind;
  platform: Platform;
  target_origin: string;
  expires_in: number;
};

export type SiteSessionSyncState = {
  request_id: string;
  target_kind: SessionSyncTargetKind;
  status: SessionSyncStatus;
  platform: Platform;
  target_origin: string;
  error_code?: string;
  message?: string;
  expires_at?: string;
  updated_at?: string;
  consumed_at?: string;
};

export type SiteCheckResponse = {
  success: boolean;
  message?: string;
  status?: SiteStatus;
  code?: string;
  browser_sync_required?: boolean;
};

export type Change = {
  id: number;
  site_id: number;
  change_type: string;
  group_name?: string | null;
  old_value?: string | null;
  new_value?: string | null;
  change_percent?: number | null;
  message?: string | null;
  created_at: string;
};

export type Overview = {
  sites_total?: number;
  sites_enabled?: number;
  sites_ok?: number;
  sites_failed?: number;
  changes_today?: number;
  [key: string]: unknown;
};

export type NotificationSettings = {
  wecom_enabled?: boolean;
  wecom_webhook?: string;
  wecom_has_webhook?: boolean;
  wecom_last_sent_at?: string | null;
  wecom_last_error?: string | null;
  email_enabled?: boolean;
  smtp_host?: string;
  smtp_port?: number;
  smtp_username?: string;
  smtp_use_ssl?: boolean;
  smtp_from?: string;
  smtp_to?: string;
  has_smtp_password?: boolean;
  email_last_sent_at?: string | null;
  email_last_error?: string | null;
};

export type AccountSubscription = {
  name?: string;
  status?: string;
  expires_at?: string | number | null;
  daily_usage_usd?: number | null;
  weekly_usage_usd?: number | null;
  monthly_usage_usd?: number | null;
  daily_limit_usd?: number | null;
  weekly_limit_usd?: number | null;
  monthly_limit_usd?: number | null;
};

export type SiteAccount = {
  platform: Platform | string;
  username?: string;
  email?: string;
  role?: string;
  status?: string;
  group?: string;
  balance_usd?: number | null;
  used_usd?: number | null;
  frozen_balance_usd?: number | null;
  total_recharged_usd?: number | null;
  request_count?: number | null;
  rpm_limit?: number | string | null;
  raw_quota?: number | null;
  raw_used_quota?: number | null;
  quota_per_unit?: number | null;
  subscriptions?: AccountSubscription[];
};

export type SiteAccountResponse = {
  success: boolean;
  source?: string;
  fetched_at?: string;
  account?: SiteAccount;
  message?: string;
};

export type ModelHealth = {
  name: string;
  status?: string;
  ratio?: number | string;
  ratio_type?: string;
  platform?: string;
  source?: string;
  channel?: string;
  monitor?: string;
  availability_7d?: number | null;
  availability_label?: string;
  ping_latency_ms?: number | null;
  latency_ms?: number | null;
  timeline?: Array<{ status?: string; checked_at?: string }>;
};

export type AdminSiteCapabilities = {
  list_channels: boolean;
  read_channel_detail: boolean;
  edit_channel: boolean;
  toggle_channel: boolean;
  create_channel: boolean;
  delete_channel: boolean;
  channel_key: boolean;
  channel_priority: boolean;
  channel_weight: boolean;
  group_rates: boolean;
  model_pricing: boolean;
};

export type Sub2ApiGroupRef = {
  id: number;
  name: string;
  platform?: string;
  status?: string;
  rate_multiplier?: number | null;
};

export type Sub2ApiPricingInterval = {
  id?: number;
  min_tokens: number;
  max_tokens?: number | null;
  tier_label?: string;
  input_price?: number | null;
  output_price?: number | null;
  cache_write_price?: number | null;
  cache_read_price?: number | null;
  per_request_price?: number | null;
  sort_order?: number;
};

export type Sub2ApiModelPricing = {
  id?: number;
  platform: string;
  models: string[];
  billing_mode: "token" | "per_request" | "image" | string;
  input_price?: number | null;
  output_price?: number | null;
  cache_write_price?: number | null;
  cache_read_price?: number | null;
  image_input_price?: number | null;
  image_output_price?: number | null;
  per_request_price?: number | null;
  intervals: Sub2ApiPricingInterval[];
};

export type Sub2ApiAccountStatsPricingRule = {
  id?: number;
  name: string;
  group_ids: number[];
  account_ids: number[];
  pricing: Sub2ApiModelPricing[];
};

/** NewAPI / sub2api 主站渠道的统一前端契约。 */
export type Channel = {
  id: number;
  name?: string;
  type?: number;
  /** 1=启用 2=手动停用 3=自动停用 */
  status?: number | string;
  description?: string;
  source_platform?: Platform | string;
  normalized_status?: "active" | "disabled" | "error" | string;
  /** 列表默认掩码，仅点「显示」时按需取明文 */
  key?: string;
  key_masked?: boolean;
  /** 逗号分隔的分组名 */
  group?: string;
  weight?: number;
  priority?: number;
  models?: string;
  base_url?: string;
  /** 模型重定向：JSON 字符串，如 {"gpt-4":"gpt-4o"} */
  model_mapping?: string | Record<string, Record<string, string>>;
  group_ids?: number[];
  groups?: Sub2ApiGroupRef[];
  model_pricing?: Sub2ApiModelPricing[];
  billing_model_source?: string;
  restrict_models?: boolean;
  features?: string | string[];
  features_config?: Record<string, unknown>;
  apply_pricing_to_account_stats?: boolean;
  account_stats_pricing_rules?: Sub2ApiAccountStatsPricingRule[];
  capabilities?: {
    edit?: boolean;
    toggle?: boolean;
    create?: boolean;
    delete?: boolean;
  };
  test_model?: string;
  response_time?: number;
  test_time?: number;
  balance?: number;
  used_quota?: number;
  tag?: string;
  auto_ban?: number;
  [key: string]: unknown;
};

export type ChannelMatchedGroup = {
  name: string;
  ratio?: number | string | null;
  ratio_type?: string;
  desc?: string;
  available_to_login?: boolean;
};

export type ChannelUpstreamBinding = {
  configured?: boolean;
  /** 渠道匹配一律复用「渠道监控」同 Base URL 上游站点的登录态 */
  inherited_from_monitor?: boolean;
  upstream_base_url?: string;
  upstream_platform?: Platform | string;
  match_status?: "unmatched" | "matched" | "matched_partial" | string;
  match_message?: string;
  matched_groups?: ChannelMatchedGroup[];
  matched_at?: string | null;
};

export type ChannelListResponse = {
  success: boolean;
  data?: Channel[];
  meta?: { total?: number; page?: number; page_size?: number };
  message?: string;
};

export type ChannelDetailResponse = {
  success: boolean;
  data?: Channel;
  message?: string;
  key_error?: string;
};

/** 分组名 → 倍率/描述（供渠道密钥比对） */
export type ChannelGroupsResponse = {
  success: boolean;
  data?: Record<string, GroupItem>;
  message?: string;
};

/** 全量渠道 key / 倍率刷新批次进度（后端进程内存态，/api/admin/sites 轮询返回） */
export type AdminKeyRefreshProgress = {
  status: "running" | "paused" | "done" | "failed" | string;
  mode: "key" | "ratio" | string;
  total: number;
  done: number;
  failed: number;
  message?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
};

/** 管理站点：统一入口，后端按 NewAPI / sub2api 平台适配。 */
export type AdminSite = {
  id: number;
  name: string;
  platform: Platform;
  platform_label?: string;
  capabilities?: AdminSiteCapabilities;
  base_url: string;
  access_user_id?: string;
  /** 后端只返回是否已配置令牌，不回传明文 */
  has_access_token?: boolean;
  login_username?: string;
  has_login_password?: boolean;
  has_sub2api_session?: boolean;
  login_last_error?: string | null;
  login_last_check_at?: string | null;
  has_security_proof?: boolean;
  security_proof_verified_at?: string | null;
  key_sync_enabled?: boolean;
  key_sync_interval_minutes?: number;
  key_sync_last_at?: string | null;
  key_sync_next_at?: string | null;
  key_sync_last_error?: string | null;
  key_sync_backoff_until?: string | null;
  key_sync_failure_count?: number;
  /** 全量 key 刷新批次进度；无批次时后端不返回该字段 */
  key_refresh?: AdminKeyRefreshProgress;
  /** 全量倍率刷新批次进度；无批次时后端不返回该字段 */
  ratio_refresh?: AdminKeyRefreshProgress;
  created_at?: string;
  updated_at?: string;
};

export type AdminSiteFormPayload = {
  platform: Platform;
  name: string;
  base_url: string;
  /** 编辑时留空表示不修改 */
  access_token: string;
  access_user_id: string;
  /** 主站网页登录账号；用于 /api/verify 和真实渠道 key 读取 */
  login_username: string;
  /** 编辑时留空表示不修改 */
  login_password: string;
  key_sync_enabled: boolean;
  key_sync_interval_minutes: number;
};

export type AdminSiteListResponse = {
  data?: AdminSite[];
};

export type SiteFormPayload = {
  name: string;
  platform: Platform;
  base_url: string;
  interval_minutes: number;
  login_enabled: boolean;
  auth_mode: AuthMode;
  login_username: string;
  login_password: string;
  access_token: string;
  refresh_token: string;
  token_expires_at: string;
  access_user_id: string;
  enabled: boolean;
  system_token_fallback_enabled: boolean;
};

/** 站点历史快照（GET /api/sites/{id}/snapshots）— 对应 snapshots 表透传 */
export type SiteSnapshot = {
  id?: number;
  site_id?: number;
  status?: string;
  source?: string;
  /** JSON 字符串或已解析的分组表；后端透传，形态宽松 */
  groups_json?: string | Record<string, GroupItem>;
  raw_json?: string | Record<string, unknown>;
  hash?: string;
  error_message?: string | null;
  checked_at?: string;
  [key: string]: unknown;
};

/** 推送历史日志（GET /api/notifications/logs）— 对应 notification_logs 表透传 */
export type NotificationLog = {
  id?: number;
  channel?: string;
  status?: string;
  target?: string;
  message?: string;
  error_message?: string | null;
  created_at?: string;
  [key: string]: unknown;
};
