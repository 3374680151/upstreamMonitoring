import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { errorText, useToast } from "@/components/Toast";
import { fmtTime } from "@/lib/format";
import type { AdminSite, AdminSiteFormPayload } from "@/lib/types";
import { Button, Field, Input, Modal, Select, SwitchRow } from "./ui";

const empty: AdminSiteFormPayload = {
  platform: "newapi",
  name: "",
  base_url: "",
  access_token: "",
  access_user_id: "",
  login_username: "",
  login_password: "",
  key_sync_enabled: false,
  key_sync_interval_minutes: 5,
};

function normalizedPayload(form: AdminSiteFormPayload): AdminSiteFormPayload {
  return {
    platform: form.platform,
    name: form.name.trim(),
    base_url: form.base_url.trim().replace(/\/+$/, ""),
    access_token:
      form.platform === "newapi" ? form.access_token.trim() : "",
    access_user_id:
      form.platform === "newapi" ? form.access_user_id.trim() : "",
    login_username: form.login_username.trim(),
    login_password: form.login_password,
    key_sync_enabled: form.platform === "newapi" && form.key_sync_enabled,
    key_sync_interval_minutes: Math.max(5, Math.min(1440, Number(form.key_sync_interval_minutes) || 5)),
  };
}

export function AdminSiteFormDialog({
  open,
  site,
  onClose,
  onSaved,
  onVerified,
}: {
  open: boolean;
  site: AdminSite | null;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
  onVerified?: () => Promise<void> | void;
}) {
  const editing = !!site;
  const toast = useToast();
  const [form, setForm] = useState<AdminSiteFormPayload>(empty);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [securityCode, setSecurityCode] = useState("");
  const [verifyingSecurity, setVerifyingSecurity] = useState(false);
  const [liveSite, setLiveSite] = useState<AdminSite | null>(site);

  useEffect(() => {
    if (!open) return;
    setLiveSite(site);
    setMsg("");
    setSecurityCode("");
    setForm(
      site
        ? {
            platform: site.platform || "newapi",
            name: site.name,
            base_url: site.base_url,
            access_token: "",
            access_user_id: site.access_user_id || "",
            login_username: site.login_username || "",
            login_password: "",
            key_sync_enabled: !!site.key_sync_enabled,
            key_sync_interval_minutes: site.key_sync_interval_minutes || 5,
          }
        : { ...empty },
    );
  }, [open, site?.id]);

  useEffect(() => {
    if (!open || !site?.id) return;
    let cancelled = false;
    const refreshStatus = async () => {
      try {
        const response = await api.adminSites();
        const latest = response.data?.find((item) => item.id === site.id) || null;
        if (!cancelled && latest) setLiveSite(latest);
      } catch {
        // 状态轮询失败不应打断正在编辑的表单。
      }
    };
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [open, site?.id]);

  const set = <K extends keyof AdminSiteFormPayload>(
    key: K,
    value: AdminSiteFormPayload[K],
  ) => setForm((previous) => ({ ...previous, [key]: value }));

  function validateCredentials(payload: AdminSiteFormPayload): string {
    if (payload.platform === "newapi") {
      const hasToken = Boolean(payload.access_token || site?.has_access_token);
      if (!hasToken || !payload.access_user_id) {
        return "请填写系统访问令牌和 NewAPI 用户 ID";
      }
      return "";
    }
    const hasPassword = Boolean(payload.login_password || site?.has_login_password);
    if (!payload.login_username || !hasPassword) {
      return "请填写 sub2api 管理员邮箱和密码";
    }
    return "";
  }

  async function testConnection() {
    const payload = normalizedPayload(form);
    if (!payload.base_url) {
      setMsg("请填写 Base URL");
      return;
    }
    const validationError = validateCredentials(payload);
    if (validationError) {
      setMsg(validationError);
      return;
    }
    setTesting(true);
    setMsg("检测中...");
    try {
      const result = await api.testAdminSite({
        ...payload,
        admin_site_id: site?.id,
      });
      const count =
        form.platform === "sub2api"
          ? `${result.channels_count ?? 0} 个渠道`
          : `${result.groups_count ?? 0} 个分组`;
      const text = `连接成功：可见 ${count}`;
      setMsg(text);
      toast.success(text);
    } catch (error) {
      const message = errorText(error, "连接失败");
      setMsg(`失败：${message}`);
      toast.error(`主站连接失败：${message}`);
    } finally {
      setTesting(false);
    }
  }

  async function persistAdminSite(payload: AdminSiteFormPayload): Promise<void> {
    if (site?.id) {
      await api.updateAdminSite(site.id, payload);
      await onSaved();
      return;
    }
    const created = await api.createAdminSite(payload);
    if (!created.id) throw new Error("后端未返回新主站 ID");
    await onSaved();
  }

  function validatedPayload(): AdminSiteFormPayload | null {
    const payload = normalizedPayload(form);
    if (!payload.name || !payload.base_url) {
      setMsg("请填写名称和 Base URL");
      return null;
    }
    const validationError = validateCredentials(payload);
    if (validationError) {
      setMsg(validationError);
      return null;
    }
    return payload;
  }

  async function save() {
    const payload = validatedPayload();
    if (!payload) return;
    setBusy(true);
    setMsg("");
    try {
      await persistAdminSite(payload);
      toast.success(
        site ? `主站「${payload.name}」已保存` : `主站「${payload.name}」已添加`,
      );
      onClose();
    } catch (error) {
      const message = errorText(error, "保存失败");
      setMsg(message);
      toast.error(`保存主站失败：${message}`);
    } finally {
      setBusy(false);
    }
  }

  async function verifyKeyAccess() {
    if (!site || site.platform !== "newapi") return;
    if (!securityCode.trim()) {
      setMsg("请输入主站当前 2FA 验证码");
      return;
    }
    setVerifyingSecurity(true);
    setMsg("验证主站 key 读取权限中...");
    try {
      const result = await api.verifyAdminSiteKeyAccess(
        site.id,
        securityCode.trim(),
      );
      if (!result.success) throw new Error(result.message || "主站安全验证失败");
      setSecurityCode("");
      setMsg("主站 key 读取权限已验证");
      toast.success("主站 key 读取权限已验证");
      await onSaved();
      await onVerified?.();
      const response = await api.adminSites();
      const latest = response.data?.find((item) => item.id === site.id);
      if (latest) setLiveSite(latest);
    } catch (error) {
      const message = errorText(error, "主站安全验证失败");
      setMsg(message);
      toast.error(message);
    } finally {
      setVerifyingSecurity(false);
    }
  }

  return (
    <Modal
      open={open}
      title={editing ? "编辑主站" : "添加主站"}
      subtitle="统一主站入口，后端按平台读取渠道配置"
      onClose={onClose}
    >
      <div className="flex flex-col gap-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="主站类型">
            <Select
              value={form.platform}
              disabled={editing}
              onChange={(event) => {
                const platform = event.target
                  .value as AdminSiteFormPayload["platform"];
                setForm((previous) => ({
                  ...previous,
                  platform,
                  access_token: "",
                  access_user_id: "",
                  login_username: "",
                  login_password: "",
                  key_sync_enabled: false,
                  key_sync_interval_minutes: 5,
                }));
              }}
            >
              <option value="newapi">NewAPI</option>
              <option value="sub2api">sub2api</option>
            </Select>
          </Field>
          <Field label="名称">
            <Input
              value={form.name}
              onChange={(event) => set("name", event.target.value)}
              maxLength={80}
              placeholder="例如 我的主站"
            />
          </Field>
        </div>

        <Field
          label={`${form.platform === "sub2api" ? "sub2api" : "NewAPI"} Base URL`}
          help="填写站点根地址，不带具体 API 路径"
        >
          <Input
            value={form.base_url}
            onChange={(event) => set("base_url", event.target.value)}
            placeholder="https://example.com"
          />
        </Field>

        {form.platform === "newapi" ? (
          <>
            <div className="grid gap-3 border-t border-line-soft pt-4 sm:grid-cols-2">
              <Field
                label="管理员系统访问令牌"
                help={
                  site?.has_access_token
                    ? "已保存，留空保持不变"
                    : "使用具备渠道读取权限的管理员令牌"
                }
              >
                <Input
                  type="password"
                  value={form.access_token}
                  onChange={(event) => set("access_token", event.target.value)}
                  autoComplete="off"
                  placeholder={site?.has_access_token ? "已保存" : "系统访问令牌"}
                />
              </Field>
              <Field label="NewAPI 用户 ID (New-Api-User)">
                <Input
                  value={form.access_user_id}
                  onChange={(event) => set("access_user_id", event.target.value)}
                  placeholder="例如 1"
                />
              </Field>
            </div>

            <div className="grid gap-3 border-t border-line-soft pt-4 sm:grid-cols-2">
              <Field label="主站网页登录账号" help="用于读取受保护的渠道 key">
                <Input
                  value={form.login_username}
                  onChange={(event) => set("login_username", event.target.value)}
                  autoComplete="username"
                />
              </Field>
              <Field
                label="主站网页登录密码"
                help={site?.has_login_password ? "已保存，留空保持不变" : undefined}
              >
                <Input
                  type="password"
                  value={form.login_password}
                  onChange={(event) => set("login_password", event.target.value)}
                  autoComplete="current-password"
                  placeholder={site?.has_login_password ? "已保存" : "登录密码"}
                />
              </Field>
            </div>

            <div className="border-t border-line-soft pt-4">
              <Field label="主站 2FA 验证码" help="用于 NewAPI 渠道 key 安全验证">
                <div className="flex flex-wrap gap-2">
                  <Input
                    className="min-w-0 flex-1"
                    value={securityCode}
                    onChange={(event) => setSecurityCode(event.target.value)}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    placeholder={site?.has_security_proof ? "已验证，可重新验证" : "当前验证码"}
                  />
                  <Button
                    variant="secondary"
                    onClick={verifyKeyAccess}
                    loading={verifyingSecurity}
                    disabled={busy || testing || !site}
                  >
                    验证 key 读取
                  </Button>
                </div>
              </Field>
            </div>

            <div className="flex flex-col gap-3 border-t border-line-soft pt-4">
              <SwitchRow
                label="自动更新渠道 key"
                checked={form.key_sync_enabled}
                onChange={(checked) => set("key_sync_enabled", checked)}
              />
              {form.key_sync_enabled ? (
                <Field
                  label="每次更新间隔（分钟）"
                  help="每次只更新 fetched_at 最旧的一个渠道；允许 5–1440 分钟"
                >
                  <Input
                    type="number"
                    min={5}
                    max={1440}
                    step={1}
                    value={form.key_sync_interval_minutes}
                    onChange={(event) =>
                      set("key_sync_interval_minutes", Number(event.target.value))
                    }
                  />
                </Field>
              ) : null}
              {liveSite?.key_sync_enabled ? (
                <div
                  className="rounded-[var(--radius-md)] border border-line bg-panel-soft px-3 py-2 text-[12px] leading-relaxed text-ink-muted"
                  aria-live="polite"
                >
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span
                      className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                        liveSite.key_sync_last_error
                          ? "bg-danger-bg text-danger-fg"
                          : liveSite.key_sync_last_at
                            ? "bg-success-bg text-success-fg"
                            : "bg-sunken text-ink-muted"
                      }`}
                    >
                      {liveSite.key_sync_last_error
                        ? "更新失败"
                        : liveSite.key_sync_last_at
                          ? "更新成功"
                          : "等待首次尝试"}
                    </span>
                    <span className="tabular-nums">
                      最近尝试：{fmtTime(liveSite.key_sync_last_at)} · 下次尝试：
                      {fmtTime(liveSite.key_sync_next_at)}
                    </span>
                  </div>
                  {liveSite.key_sync_last_error ? (
                    <div className="mt-1 text-danger-fg">
                      失败原因：{liveSite.key_sync_last_error}
                      {liveSite.key_sync_backoff_until
                        ? ` · 暂停至 ${fmtTime(liveSite.key_sync_backoff_until)}`
                        : ""}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </>
        ) : (
          <div className="grid gap-3 border-t border-line-soft pt-4 sm:grid-cols-2">
            <Field label="sub2api 管理员邮箱">
              <Input
                type="email"
                value={form.login_username}
                onChange={(event) => set("login_username", event.target.value)}
                autoComplete="username"
                placeholder="admin@example.com"
              />
            </Field>
            <Field
              label="sub2api 管理员密码"
              help={site?.has_login_password ? "已保存，留空保持不变" : undefined}
            >
              <Input
                type="password"
                value={form.login_password}
                onChange={(event) => set("login_password", event.target.value)}
                autoComplete="current-password"
                placeholder={site?.has_login_password ? "已保存" : "管理员密码"}
              />
            </Field>
            {site?.has_sub2api_session ? (
              <div className="text-[12.5px] text-success-fg sm:col-span-2">
                管理员登录态可用
              </div>
            ) : site?.login_last_error ? (
              <div className="text-[12.5px] text-danger-fg sm:col-span-2">
                最近登录失败：{site.login_last_error}
              </div>
            ) : null}
          </div>
        )}

        <div className="flex flex-wrap gap-2 border-t border-line-soft pt-4">
          <Button
            variant="secondary"
            onClick={testConnection}
            loading={testing}
            disabled={busy}
          >
            测试连接
          </Button>
          <Button onClick={save} loading={busy} disabled={testing}>
            保存
          </Button>
        </div>
        {msg ? (
          <div className="rounded-[var(--radius-md)] bg-sunken px-3 py-2 text-[12.5px] text-ink-muted">
            {msg}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
