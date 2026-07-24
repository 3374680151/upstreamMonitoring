import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AuthMode, Platform, Site, SiteFormPayload } from "@/lib/types";
import { Button, Field, Input, Modal, Select, SwitchRow } from "./ui";

const empty: SiteFormPayload = {
  name: "",
  platform: "newapi",
  base_url: "",
  interval_minutes: 3,
  login_enabled: false,
  auth_mode: "password",
  login_username: "",
  login_password: "",
  access_token: "",
  refresh_token: "",
  token_expires_at: "",
  access_user_id: "",
  enabled: true,
};

export function SiteFormDialog({
  open,
  site,
  onClose,
  onSaved,
}: {
  open: boolean;
  site: Site | null;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
}) {
  const [form, setForm] = useState<SiteFormPayload>(empty);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setMsg("");
    if (site) {
      setForm({
        ...empty,
        name: site.name,
        platform: (site.platform as Platform) || "newapi",
        base_url: site.base_url,
        interval_minutes: site.interval_minutes || 3,
        login_enabled: !!site.login_enabled,
        auth_mode: (site.auth_mode as AuthMode) || "password",
        login_username: site.login_username || "",
        access_user_id: site.access_user_id || "",
        token_expires_at: site.token_expires_at || "",
        enabled: !!site.enabled,
      });
    } else {
      setForm(empty);
    }
  }, [open, site]);

  const set = <K extends keyof SiteFormPayload>(key: K, value: SiteFormPayload[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const isSub2api = form.platform === "sub2api";
  const tokenMode = form.auth_mode === "token";

  async function save() {
    setBusy(true);
    setMsg("");
    try {
      const payload: SiteFormPayload = {
        ...form,
        login_enabled: isSub2api ? true : form.login_enabled,
        auth_mode: isSub2api ? form.auth_mode : "password",
      };
      if (site) await api.updateSite(site.id, payload);
      else await api.createSite(payload);
      await onSaved();
      onClose();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function testConnection() {
    const baseUrl = form.base_url.trim().replace(/\/+$/, "");
    if (!baseUrl) {
      setMsg("请先填写 Base URL");
      return;
    }
    if (isSub2api && !tokenMode && (!form.login_username.trim() || !form.login_password)) {
      setMsg("请填写 sub2api 用户邮箱和密码");
      return;
    }
    if (isSub2api && tokenMode && !form.access_token.trim()) {
      setMsg("请填写 sub2api auth_token");
      return;
    }
    setMsg("检测中...");
    try {
      const res = await api.checkConnection({
        platform: form.platform,
        base_url: baseUrl,
        auth_mode: form.auth_mode,
        login_username: form.login_username.trim(),
        login_password: form.login_password,
        access_token: form.access_token.trim(),
        refresh_token: form.refresh_token.trim(),
      });
      setMsg(
        res.success
          ? `连接成功：${res.groups_count ?? 0} 个分组`
          : `失败：${res.message}`,
      );
    } catch (err) {
      setMsg(`失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  async function testAuth() {
    const baseUrl = form.base_url.trim().replace(/\/+$/, "");
    if (isSub2api) {
      return testConnection();
    }
    if (!baseUrl || !form.access_token.trim() || !form.access_user_id.trim()) {
      setMsg("请填写 Base URL、系统访问令牌和 NewAPI 用户 ID");
      return;
    }
    setMsg("访问令牌测试中...");
    try {
      const res = await api.checkLogin({
        base_url: baseUrl,
        access_token: form.access_token.trim(),
        access_user_id: form.access_user_id.trim(),
      });
      setMsg(
        res.success
          ? `验证成功：认证后可见 ${res.groups_count ?? 0} 个分组`
          : `验证失败：${res.message}`,
      );
    } catch (err) {
      setMsg(`验证失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  return (
    <Modal
      open={open}
      title={site ? "编辑站点" : "添加站点"}
      subtitle="配置 NewAPI / sub2api 上游与认证方式"
      onClose={onClose}
    >
      <div className="space-y-3">
        <Field label="站点名称">
          <Input value={form.name} onChange={(e) => set("name", e.target.value)} maxLength={80} />
        </Field>
        <Field label="平台类型">
          <Select
            value={form.platform}
            onChange={(e) => set("platform", e.target.value as Platform)}
          >
            <option value="newapi">NewAPI</option>
            <option value="sub2api">sub2api</option>
          </Select>
        </Field>
        <Field label="Base URL" help="例如 https://example.com，不要带具体 API 路径">
          <Input
            value={form.base_url}
            onChange={(e) => set("base_url", e.target.value)}
            placeholder="https://example.com"
          />
        </Field>
        <Field label="监控间隔（分钟）">
          <Input
            type="number"
            min={1}
            value={form.interval_minutes}
            onChange={(e) => set("interval_minutes", Math.max(1, Number(e.target.value || 3)))}
          />
        </Field>

        {!isSub2api ? (
          <div className="space-y-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-panel-soft)] p-3">
            <SwitchRow
              label="认证增强监控"
              checked={form.login_enabled}
              onChange={(v) => set("login_enabled", v)}
            />
            <p className="text-[11px] text-[var(--color-text-soft)]">
              填写系统访问令牌和 NewAPI 用户 ID 后，可查看隐藏/专属分组。
            </p>
            {form.login_enabled ? (
              <div className="space-y-3">
                <Field label="系统访问令牌" help="编辑时留空表示不修改">
                  <Input
                    type="password"
                    value={form.access_token}
                    onChange={(e) => set("access_token", e.target.value)}
                    autoComplete="off"
                  />
                </Field>
                <Field label="NewAPI 用户 ID">
                  <Input
                    value={form.access_user_id}
                    onChange={(e) => set("access_user_id", e.target.value)}
                    placeholder="例如：4"
                  />
                </Field>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="space-y-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-panel-soft)] p-3">
            <p className="text-[11px] text-[var(--color-text-soft)]">
              sub2api 支持账号密码登录，也支持导入浏览器登录态。开启 Turnstile 时建议导入登录态。
            </p>
            <Field label="认证方式">
              <Select
                value={form.auth_mode}
                onChange={(e) => set("auth_mode", e.target.value as AuthMode)}
              >
                <option value="password">账号密码登录</option>
                <option value="token">导入登录态</option>
              </Select>
            </Field>
            {!tokenMode ? (
              <>
                <Field label="用户邮箱">
                  <Input
                    value={form.login_username}
                    onChange={(e) => set("login_username", e.target.value)}
                    placeholder="user@example.com"
                  />
                </Field>
                <Field label="用户密码" help="编辑时留空表示不修改">
                  <Input
                    type="password"
                    value={form.login_password}
                    onChange={(e) => set("login_password", e.target.value)}
                  />
                </Field>
              </>
            ) : (
              <>
                <Field label="auth_token" help="编辑时留空表示不修改">
                  <Input
                    type="password"
                    value={form.access_token}
                    onChange={(e) => set("access_token", e.target.value)}
                  />
                </Field>
                <Field label="refresh_token" help="可选，token 过期可自动刷新">
                  <Input
                    type="password"
                    value={form.refresh_token}
                    onChange={(e) => set("refresh_token", e.target.value)}
                  />
                </Field>
                <Field label="token_expires_at">
                  <Input
                    value={form.token_expires_at}
                    onChange={(e) => set("token_expires_at", e.target.value)}
                  />
                </Field>
              </>
            )}
          </div>
        )}

        <SwitchRow
          label="启用监控"
          checked={form.enabled}
          onChange={(v) => set("enabled", v)}
        />

        <div className="flex flex-wrap gap-2 pt-1">
          <Button type="button" variant="secondary" onClick={testConnection}>
            测试连接
          </Button>
          <Button type="button" variant="secondary" onClick={testAuth}>
            {isSub2api ? (tokenMode ? "测试登录态" : "测试登录") : "测试认证"}
          </Button>
          <Button type="button" onClick={save} disabled={busy}>
            {busy ? "保存中..." : "保存"}
          </Button>
        </div>
        {msg ? (
          <div className="rounded-xl bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--color-text-muted)]">
            {msg}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
