import { useEffect, useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import {
  probeSessionBridge,
  syncSiteBrowserSession,
} from "@/lib/browserSessionBridge";
import { errorText, useToast } from "@/components/Toast";
import { ChannelDiscoveryPanel } from "./ChannelDiscoveryPanel";
import type {
  AuthMode,
  Platform,
  Site,
  SiteFormPayload,
  SiteSessionSyncState,
} from "@/lib/types";
import { Button, Field, Input, Modal, Select, SwitchRow, Tabs } from "./ui";

const empty: SiteFormPayload = {
  name: "",
  platform: "newapi",
  base_url: "",
  interval_minutes: 3,
  login_enabled: false,
  auth_mode: "token",
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
  onEditSite,
}: {
  open: boolean;
  site: Site | null;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
  onEditSite?: (siteId: number) => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<SiteFormPayload>(empty);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [authTesting, setAuthTesting] = useState(false);
  const [savedSiteId, setSavedSiteId] = useState<number | null>(null);
  const [syncResult, setSyncResult] = useState<SiteSessionSyncState | null>(null);
  const [mode, setMode] = useState<"manual" | "discovery">("manual");
  const [twoFactorCode, setTwoFactorCode] = useState("");
  const [needsTwoFactor, setNeedsTwoFactor] = useState(false);

  useEffect(() => {
    if (!open) return;
    setMsg("");
    setSavedSiteId(site?.id ?? null);
    setSyncResult(null);
    setTwoFactorCode("");
    setNeedsTwoFactor(false);
    setMode("manual");
    if (site) {
      setForm({
        ...empty,
        name: site.name,
        platform: (site.platform as Platform) || "newapi",
        base_url: site.base_url,
        interval_minutes: site.interval_minutes || 3,
        login_enabled: !!site.login_enabled,
        auth_mode: (site.auth_mode as AuthMode) || "token",
        login_username: site.login_username || "",
        access_user_id: site.access_user_id || "",
        token_expires_at: site.token_expires_at || "",
        enabled: !!site.enabled,
      });
    } else {
      setForm(empty);
    }
    // 只在打开弹窗或切换编辑目标时初始化。父级轮询会刷新 site 对象，
    // 但不应该覆盖用户已经填写了一半的本地草稿。
  }, [open, site?.id]);

  const set = <K extends keyof SiteFormPayload>(key: K, value: SiteFormPayload[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const setPlatform = (platform: Platform) => {
    setSyncResult(null);
    setForm((prev) =>
      platform === "sub2api"
        ? {
            ...prev,
            platform,
            login_enabled: true,
            auth_mode: "browser",
            login_username: "",
            login_password: "",
            access_token: "",
            refresh_token: "",
            token_expires_at: "",
            access_user_id: "",
          }
        : {
            ...prev,
            platform,
            login_enabled: false,
            auth_mode: "token",
            login_username: "",
            login_password: "",
            access_token: "",
            refresh_token: "",
            token_expires_at: "",
            access_user_id: "",
          },
    );
  };

  const isSub2api = form.platform === "sub2api";
  const tokenMode = form.auth_mode === "token";
  const browserMode = form.auth_mode === "browser";
  const passwordMode = form.auth_mode === "password";
  const newApiPasswordMode = !isSub2api && passwordMode;
  const sameSavedPlatform = Boolean(site && site.platform === form.platform);
  const sameSavedAuthMode = Boolean(
    sameSavedPlatform &&
      (site?.auth_mode || "token") === form.auth_mode,
  );
  const hasSavedNewApiToken = Boolean(
    sameSavedAuthMode && !isSub2api && tokenMode && site?.has_access_token,
  );
  const hasSavedNewApiPassword = Boolean(
    sameSavedAuthMode && !isSub2api && passwordMode && site?.has_login_password,
  );
  const hasSavedSub2ApiToken = Boolean(
    sameSavedAuthMode && isSub2api && tokenMode && site?.has_access_token,
  );
  const hasSavedSub2ApiPassword = Boolean(
    sameSavedPlatform &&
      isSub2api &&
      (passwordMode || browserMode) &&
      site?.has_login_password,
  );
  const hasSavedSub2ApiRefresh = Boolean(
    sameSavedAuthMode && isSub2api && tokenMode && site?.has_refresh_token,
  );
  const savedTokenHelp = site && (hasSavedNewApiToken || hasSavedSub2ApiToken)
    ? "当前已有令牌，留空保持不变；填写新值会替换原令牌"
    : "仅用于读取上游数据，不需要管理员权限";
  const savedPasswordHelp = (hasSavedSub2ApiPassword || hasSavedNewApiPassword)
    ? "当前已有密码，留空保持不变；填写新值会替换原密码"
    : "尚未配置，填写后启用账号密码登录";

  async function runBrowserSync(targetSiteId: number): Promise<boolean> {
    setMsg("正在查找浏览器登录态");
    setSyncResult(null);
    const result = await syncSiteBrowserSession(targetSiteId);
    setSyncResult(result);
    await onSaved();
    if (result.status === "ready") {
      setMsg("浏览器登录态已同步，首次检测已完成");
      toast.success(`渠道「${form.name}」登录态已同步`);
      onClose();
      return true;
    }
    const message = result.message || result.error_code || "登录态同步失败";
    setMsg(message);
    toast.info(`渠道已保存：${message}`);
    return false;
  }

  async function runNewApiPasswordLogin(targetSiteId: number): Promise<boolean> {
    setMsg("正在验证 NewAPI 用户名密码");
    const result = await api.loginNewApiSite(targetSiteId, twoFactorCode.trim());
    if (result.requires_2fa) {
      setNeedsTwoFactor(true);
      setMsg(result.message || "需要 2FA 验证码");
      return false;
    }
    if (!result.success) {
      throw new Error(result.message || "NewAPI 用户名密码验证失败");
    }
    setNeedsTwoFactor(false);
    setTwoFactorCode("");
    const suffix = result.warning ? `；${result.warning}` : "";
    setMsg(`登录验证成功：${result.groups_count ?? 0} 个分组${suffix}`);
    return true;
  }

  async function save() {
    setBusy(true);
    setMsg("");
    try {
      const payload: SiteFormPayload = {
        ...form,
        login_enabled:
          isSub2api || form.login_enabled || newApiPasswordMode || browserMode,
        // NewAPI now supports the same browser/password session modes as the
        // discovery flow. Keep the selected mode when saving; coercing it to
        // token would immediately invalidate a newly synced browser session.
        auth_mode: form.auth_mode,
      };
      let targetSiteId = site?.id ?? savedSiteId;
      if (site || savedSiteId) {
        if (!targetSiteId) throw new Error("渠道 ID 无效");
        await api.updateSite(targetSiteId, payload);
      } else {
        const created = await api.createSite(payload);
        if (!created.id) throw new Error("后端未返回新渠道 ID");
        targetSiteId = created.id;
        setSavedSiteId(created.id);
      }
      if (browserMode) {
        await runBrowserSync(targetSiteId);
        return;
      }
      if (newApiPasswordMode) {
        const loggedIn = await runNewApiPasswordLogin(targetSiteId);
        if (!loggedIn) return;
      }
      await onSaved();
      toast.success(site ? `渠道「${payload.name}」已保存` : `渠道「${payload.name}」已添加`);
      onClose();
    } catch (err) {
      const message = errorText(err, "保存失败");
      setMsg(message);
      toast.error(`保存渠道失败：${message}`);
    } finally {
      setBusy(false);
    }
  }

  async function retryBrowserSync() {
    const targetSiteId = site?.id ?? savedSiteId;
    if (!targetSiteId) return;
    setBusy(true);
    try {
      await runBrowserSync(targetSiteId);
    } catch (err) {
      const message = errorText(err, "登录态同步失败");
      setMsg(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  }

  function openUpstreamLogin() {
    const url = form.base_url.trim().replace(/\/+$/, "");
    if (url) window.open(url, "_blank", "noopener,noreferrer");
  }

  async function testBrowserBridge() {
    setTesting(true);
    try {
      const available = await probeSessionBridge();
      setMsg(
        available
          ? "浏览器同步扩展已连接"
          : "浏览器同步扩展未连接或版本过旧，请重新加载桌面项目中的 0.1.2 扩展并刷新页面",
      );
    } finally {
      setTesting(false);
    }
  }

  async function testConnection() {
    const baseUrl = form.base_url.trim().replace(/\/+$/, "");
    if (!baseUrl) {
      setMsg("请先填写 Base URL");
      return;
    }
    if (
      isSub2api &&
      passwordMode &&
      (!form.login_username.trim() || !form.login_password)
    ) {
      setMsg("请填写 sub2api 用户邮箱和密码");
      return;
    }
    if (isSub2api && tokenMode && !form.access_token.trim()) {
      setMsg("请填写 sub2api auth_token");
      return;
    }
    setTesting(true);
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
      if (res.success) {
        const text = `连接成功：${res.groups_count ?? 0} 个分组`;
        setMsg(text);
        toast.success(text);
      } else {
        const text = res.message || "连接失败";
        setMsg(`失败：${text}`);
        toast.error(`连接失败：${text}`);
      }
    } catch (err) {
      const message = errorText(err, "连接失败");
      setMsg(`失败：${message}`);
      toast.error(`连接失败：${message}`);
    } finally {
      setTesting(false);
    }
  }

  async function testAuth() {
    const baseUrl = form.base_url.trim().replace(/\/+$/, "");
    if (browserMode) return testBrowserBridge();
    if (isSub2api) {
      return testConnection();
    }
    if (
      newApiPasswordMode &&
      (!baseUrl || !form.login_username.trim() || !form.login_password)
    ) {
      setMsg("请填写 Base URL、NewAPI 用户名和密码");
      return;
    }
    if (
      !newApiPasswordMode &&
      (!baseUrl || !form.access_token.trim() || !form.access_user_id.trim())
    ) {
      setMsg("请填写 Base URL、系统访问令牌和 NewAPI 用户 ID");
      return;
    }
    setAuthTesting(true);
    setMsg(newApiPasswordMode ? "用户名密码验证中..." : "访问令牌测试中...");
    try {
      const res = await api.checkLogin({
        base_url: baseUrl,
        auth_mode: form.auth_mode,
        login_username: form.login_username.trim(),
        login_password: form.login_password,
        two_factor_code: twoFactorCode.trim(),
        access_token: form.access_token.trim(),
        access_user_id: form.access_user_id.trim(),
      });
      if (res.requires_2fa) {
        setNeedsTwoFactor(true);
        setMsg(res.message || "需要 2FA 验证码");
        return;
      }
      if (res.success) {
        const text = `验证成功：认证后可见 ${res.groups_count ?? 0} 个分组`;
        setMsg(text);
        toast.success(text);
      } else {
        const text = res.message || "验证失败";
        setMsg(`验证失败：${text}`);
        toast.error(`${newApiPasswordMode ? "用户名密码" : "令牌"}验证失败：${text}`);
      }
    } catch (err) {
      const message = errorText(err, "验证失败");
      setMsg(`验证失败：${message}`);
      toast.error(`${newApiPasswordMode ? "用户名密码" : "令牌"}验证失败：${message}`);
    } finally {
      setAuthTesting(false);
    }
  }

  return (
    <Modal
      open={open}
      title={site ? "编辑渠道" : "添加渠道"}
      subtitle="配置 NewAPI / sub2api 上游渠道与认证方式"
      onClose={onClose}
      wide={!site && mode === "discovery"}
    >
      {!site ? (
        <div className="mb-4">
          <Tabs
            label="添加渠道方式"
            items={[
              { id: "manual", label: "手动添加" },
              { id: "discovery", label: "从主站发现" },
            ]}
            value={mode}
            onChange={setMode}
          />
        </div>
      ) : null}
      {!site && mode === "discovery" ? (
        <ChannelDiscoveryPanel
          open
          onClose={() => setMode("manual")}
          onImported={onSaved}
          onEditSite={(siteId) => onEditSite?.(siteId)}
        />
      ) : (
      <div className="space-y-3">
        <Field label="渠道名称">
          <Input value={form.name} onChange={(e) => set("name", e.target.value)} maxLength={80} />
        </Field>
        <Field label="平台类型">
          <Select
            value={form.platform}
            onChange={(e) => setPlatform(e.target.value as Platform)}
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
          <div className="space-y-3 rounded-2xl border border-line bg-panel-soft p-3">
            <SwitchRow
              label="认证增强监控"
              checked={form.login_enabled}
              onChange={(loginEnabled) =>
                setForm((previous) => ({
                  ...previous,
                  login_enabled: loginEnabled,
                  auth_mode: loginEnabled ? previous.auth_mode : "token",
                }))
              }
            />
            <p className="text-[11px] text-ink-soft">
              填写<b>普通用户</b>的系统访问令牌和 NewAPI 用户 ID 后，可查看隐藏/专属分组与账户额度。
              这些接口（<code>/api/user/self</code>、<code>/api/user/self/groups</code>）只要普通用户权限，
              <b>不要填管理员令牌</b>——这是别人家的上游，令牌泄露会连带暴露渠道/用户/日志管理权限。
              主站监控中的真实渠道会按 Base URL 自动匹配并复用这里的登录态，用于读取该上游账号的分组和倍率。
              真实主站渠道的新增、删除和其他配置请在主站后台完成。
            </p>
            {form.login_enabled ? (
              <div className="space-y-3">
                <Field label="认证方式">
                  <Select
                    value={form.auth_mode}
                    onChange={(event) => {
                      set("auth_mode", event.target.value as AuthMode);
                      setNeedsTwoFactor(false);
                      setTwoFactorCode("");
                    }}
                  >
                    <option value="token">手动系统访问令牌</option>
                    <option value="password">用户名密码登录</option>
                    <option value="browser">浏览器登录态同步</option>
                  </Select>
                </Field>
                {tokenMode ? (
                  <>
                    <Field
                      label="系统访问令牌（普通用户即可）"
                      help={hasSavedNewApiToken ? savedTokenHelp : "尚未配置，填写后可读取余额与隐藏分组"}
                    >
                      <Input
                        type="password"
                        value={form.access_token}
                        onChange={(e) => set("access_token", e.target.value)}
                        autoComplete="off"
                        placeholder={hasSavedNewApiToken ? "已保存，留空不修改" : "填写普通用户令牌"}
                      />
                    </Field>
                    <Field label="NewAPI 用户 ID">
                      <Input
                        value={form.access_user_id}
                        onChange={(e) => set("access_user_id", e.target.value)}
                        placeholder="例如：4"
                      />
                    </Field>
                  </>
                ) : passwordMode ? (
                  <>
                    <Field label="NewAPI 用户名">
                      <Input
                        value={form.login_username}
                        onChange={(e) => set("login_username", e.target.value)}
                        autoComplete="username"
                      />
                    </Field>
                    <Field label="NewAPI 密码" help={savedPasswordHelp}>
                      <Input
                        type="password"
                        value={form.login_password}
                        onChange={(e) => set("login_password", e.target.value)}
                        autoComplete="current-password"
                        placeholder={hasSavedNewApiPassword ? "已保存，留空不修改" : "填写用户密码"}
                      />
                    </Field>
                    {needsTwoFactor ? (
                      <Field label="2FA 验证码" help="验证码仅用于本次登录，不会保存">
                        <Input
                          value={twoFactorCode}
                          onChange={(e) => setTwoFactorCode(e.target.value)}
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          placeholder="输入当前动态验证码"
                        />
                      </Field>
                    ) : null}
                  </>
                ) : (
                  <div className="rounded-[var(--radius-md)] border border-line bg-info-bg px-3 py-2.5 text-[12.5px] text-info-fg">
                    在 Chrome 登录上游后保存，系统会同步当前登录态。适用于拦截后端 Python 请求的站点。
                  </div>
                )}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="space-y-3 rounded-2xl border border-line bg-panel-soft p-3">
            <p className="text-[11px] text-ink-soft">
              sub2api 默认从当前 Chrome 同步已经验证过的人机验证登录态；也保留账号密码和手动 token 模式。
            </p>
            <Field label="认证方式">
              <Select
                value={form.auth_mode}
                onChange={(e) => set("auth_mode", e.target.value as AuthMode)}
              >
                <option value="browser">浏览器自动同步（推荐）</option>
                <option value="password">账号密码登录</option>
                <option value="token">手动导入登录态</option>
              </Select>
            </Field>
            {browserMode ? (
              <>
                <div className="rounded-[var(--radius-md)] border border-line bg-success-bg px-3 py-2.5 text-[12.5px] text-success-fg">
                  浏览器登录态 → refresh_token → 账号密码
                  {site?.session_synced_at ? (
                    <span className="mt-1 block text-[11px] opacity-80">
                      最近同步：{site.session_synced_at}
                    </span>
                  ) : null}
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="兜底用户邮箱（可选）">
                    <Input
                      value={form.login_username}
                      onChange={(e) => set("login_username", e.target.value)}
                      placeholder="user@example.com"
                    />
                  </Field>
                  <Field label="兜底用户密码（可选）" help={savedPasswordHelp}>
                    <Input
                      type="password"
                      value={form.login_password}
                      onChange={(e) => set("login_password", e.target.value)}
                      placeholder={
                        hasSavedSub2ApiPassword
                          ? "已保存，留空不修改"
                          : "未配置"
                      }
                      autoComplete="new-password"
                    />
                  </Field>
                </div>
              </>
            ) : passwordMode ? (
              <>
                <Field label="用户邮箱">
                  <Input
                    value={form.login_username}
                    onChange={(e) => set("login_username", e.target.value)}
                    placeholder="user@example.com"
                  />
                </Field>
                <Field label="用户密码" help={savedPasswordHelp}>
                  <Input
                    type="password"
                    value={form.login_password}
                    onChange={(e) => set("login_password", e.target.value)}
                    placeholder={hasSavedSub2ApiPassword ? "已保存，留空不修改" : "填写用户密码"}
                  />
                </Field>
              </>
            ) : tokenMode ? (
              <>
                <Field label="auth_token" help={hasSavedSub2ApiToken ? savedTokenHelp : "尚未配置，填写后可导入登录态"}>
                  <Input
                    type="password"
                    value={form.access_token}
                    onChange={(e) => set("access_token", e.target.value)}
                    placeholder={hasSavedSub2ApiToken ? "已保存，留空不修改" : "填写 auth_token"}
                  />
                </Field>
                <Field
                  label="refresh_token"
                  help={hasSavedSub2ApiRefresh ? "当前已有 refresh_token，留空保持不变" : "可选，token 过期可自动刷新"}
                >
                  <Input
                    type="password"
                    value={form.refresh_token}
                    onChange={(e) => set("refresh_token", e.target.value)}
                    placeholder={hasSavedSub2ApiRefresh ? "已保存，留空不修改" : "可选"}
                  />
                </Field>
                <Field label="token_expires_at">
                  <Input
                    value={form.token_expires_at}
                    onChange={(e) => set("token_expires_at", e.target.value)}
                  />
                </Field>
              </>
            ) : null}
          </div>
        )}

        <SwitchRow
          label="启用监控"
          checked={form.enabled}
          onChange={(v) => set("enabled", v)}
        />

        <div className="flex flex-wrap gap-2 pt-1">
          {browserMode ? (
            <Button
              type="button"
              variant="secondary"
              className="h-8"
              onClick={testBrowserBridge}
              loading={testing}
              disabled={busy}
            >
              检测同步扩展
            </Button>
          ) : (
            <>
              <Button
                type="button"
                variant="secondary"
                onClick={testConnection}
                loading={testing}
                disabled={busy || authTesting}
              >
                测试连接
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={testAuth}
                loading={authTesting}
                disabled={busy || testing}
              >
                {isSub2api ? (tokenMode ? "测试登录态" : "测试登录") : "测试认证"}
              </Button>
            </>
          )}
          <Button
            type="button"
            className="h-8"
            onClick={save}
            loading={busy}
            disabled={testing || authTesting}
          >
            保存
          </Button>
        </div>
        {browserMode && syncResult && syncResult.status !== "ready" ? (
          <div className="flex flex-wrap gap-2 rounded-xl border border-line bg-panel-soft p-3">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="h-8"
              onClick={openUpstreamLogin}
              disabled={busy}
            >
              <ExternalLink size={13} />
              打开上游登录页
            </Button>
            <Button
              type="button"
              variant="brand"
              size="sm"
              className="h-8"
              onClick={retryBrowserSync}
              loading={busy}
            >
              {busy ? null : <RefreshCw size={13} />}
              重新同步
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="h-8"
              onClick={onClose}
              disabled={busy}
            >
              稍后处理
            </Button>
          </div>
        ) : null}
        {msg ? (
          <div className="rounded-xl bg-sunken px-3 py-2 text-[12.5px] text-ink-muted">
            {msg}
          </div>
        ) : null}
      </div>
      )}
    </Modal>
  );
}
