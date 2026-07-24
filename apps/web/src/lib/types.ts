export type Platform = "newapi" | "sub2api";
export type AuthMode = "password" | "token";
export type SiteStatus = "ok" | "warning" | "failed" | "unknown" | string;

export type GroupItem = {
  ratio?: number | string;
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
  has_refresh_token?: boolean;
  token_expires_at?: string | null;
  login_last_error?: string | null;
  login_last_check_at?: string | null;
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
};
