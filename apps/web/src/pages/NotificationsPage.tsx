import { useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { Button, Field, Input, SwitchRow } from "@/components/ui";
import { errorText, useToast } from "@/components/Toast";
import { api } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { NotificationSettings } from "@/lib/types";

type NotificationForm = {
  wecom_enabled: boolean;
  wecom_webhook: string;
  email_enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_username: string;
  smtp_password: string;
  smtp_use_ssl: boolean;
  smtp_from: string;
  smtp_to: string;
};

const EMPTY_FORM: NotificationForm = {
  wecom_enabled: false,
  wecom_webhook: "",
  email_enabled: false,
  smtp_host: "",
  smtp_port: 465,
  smtp_username: "",
  smtp_password: "",
  smtp_use_ssl: true,
  smtp_from: "",
  smtp_to: "",
};

export function NotificationsPage({
  settings,
  onReload,
}: {
  settings: NotificationSettings | null;
  onReload: () => Promise<void> | void;
}) {
  const [form, setForm] = useState<NotificationForm>(EMPTY_FORM);
  const formDirty = useRef(false);
  const [wecomStatus, setWecomStatus] = useState("未配置");
  const [emailStatus, setEmailStatus] = useState("未配置");
  const [wecomError, setWecomError] = useState(false);
  const [emailError, setEmailError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [emailTesting, setEmailTesting] = useState(false);
  const [wecomTesting, setWecomTesting] = useState(false);
  const toast = useToast();

  useEffect(() => {
    if (!settings) return;
    if (!formDirty.current) {
      setForm({
        ...EMPTY_FORM,
        wecom_enabled: !!settings.wecom_enabled,
        wecom_webhook: settings.wecom_webhook || "",
        email_enabled: !!settings.email_enabled,
        smtp_host: settings.smtp_host || "",
        smtp_port: settings.smtp_port || 465,
        smtp_username: settings.smtp_username || "",
        smtp_use_ssl: settings.smtp_use_ssl !== false,
        smtp_from: settings.smtp_from || "",
        smtp_to: settings.smtp_to || "",
      });
    }
    const wecomParts = [
      settings.wecom_enabled ? "企业微信已启用" : "企业微信未启用",
    ];
    if (settings.wecom_has_webhook) wecomParts.push("Webhook 已保存");
    if (settings.wecom_last_sent_at) {
      wecomParts.push(`上次发送：${fmtTime(settings.wecom_last_sent_at)}`);
    }
    if (settings.wecom_last_error) {
      wecomParts.push(`错误：${settings.wecom_last_error}`);
    }
    setWecomStatus(wecomParts.join(" · "));
    setWecomError(!!settings.wecom_last_error);

    const emailParts = [
      settings.email_enabled ? "邮箱推送已启用" : "邮箱推送未启用",
    ];
    if (settings.has_smtp_password) emailParts.push("密码已保存");
    if (settings.email_last_sent_at) {
      emailParts.push(`上次发送：${fmtTime(settings.email_last_sent_at)}`);
    }
    if (settings.email_last_error) {
      emailParts.push(`错误：${settings.email_last_error}`);
    }
    setEmailStatus(emailParts.join(" · "));
    setEmailError(!!settings.email_last_error);
  }, [settings]);

  function updateForm(updater: (previous: NotificationForm) => NotificationForm) {
    formDirty.current = true;
    setForm(updater);
  }

  const payload = () => ({
    wecom_enabled: form.wecom_enabled,
    wecom_webhook: form.wecom_webhook.trim(),
    email_enabled: form.email_enabled,
    smtp_host: form.smtp_host.trim(),
    smtp_port: Math.max(1, Number(form.smtp_port || 465)),
    smtp_username: form.smtp_username.trim(),
    smtp_password: form.smtp_password,
    smtp_use_ssl: form.smtp_use_ssl,
    smtp_from: form.smtp_from.trim(),
    smtp_to: form.smtp_to.trim(),
  });

  async function save() {
    setSaving(true);
    setEmailStatus("保存中...");
    setEmailError(false);
    try {
      await api.saveNotificationSettings(payload());
      formDirty.current = false;
      await onReload();
      // 成功也要落地：以前只留一句「保存中...」，看起来像卡住了
      setEmailStatus("设置已保存");
      setEmailError(false);
      toast.success("推送设置已保存");
    } catch (err) {
      const message = errorText(err, "保存失败");
      setEmailStatus(`保存失败：${message}`);
      setEmailError(true);
      toast.error(`保存推送设置失败：${message}`);
    } finally {
      setSaving(false);
    }
  }

  async function testEmail() {
    setEmailTesting(true);
    setEmailStatus("测试邮件发送中...");
    setEmailError(false);
    try {
      const res = await api.testEmail(payload());
      // 后端用 success=false 表达业务失败（HTTP 仍是 200），必须据此判定
      const message = res.message || (res.success ? "测试邮件已发送" : "测试失败");
      setEmailStatus(message);
      setEmailError(!res.success);
      if (res.success) toast.success(message);
      else toast.error(`邮件测试失败：${message}`);
      await onReload();
    } catch (err) {
      const message = errorText(err, "测试失败");
      setEmailStatus(`测试失败：${message}`);
      setEmailError(true);
      toast.error(`邮件测试失败：${message}`);
    } finally {
      setEmailTesting(false);
    }
  }

  async function testWecom() {
    setWecomTesting(true);
    setWecomStatus("测试企业微信发送中...");
    setWecomError(false);
    try {
      const res = await api.testWecom(payload());
      const message = res.message || (res.success ? "测试消息已发送" : "测试失败");
      setWecomStatus(message);
      setWecomError(!res.success);
      if (res.success) toast.success(message);
      else toast.error(`企业微信测试失败：${message}`);
      await onReload();
    } catch (err) {
      const message = errorText(err, "测试失败");
      setWecomStatus(`测试失败：${message}`);
      setWecomError(true);
      toast.error(`企业微信测试失败：${message}`);
    } finally {
      setWecomTesting(false);
    }
  }

  return (
    <div className="upstream-rise space-y-6">
      <PageHeader
        title="消息推送"
        subtitle="支持企业微信和邮箱推送，检测到倍率或分组变化后自动发送提醒。"
      />

      <Panel title="企业微信" subtitle="群机器人 Webhook，无需服务器回调">
        <div className="space-y-3">
          <SwitchRow
            label="启用企业微信推送"
            checked={form.wecom_enabled}
            onChange={(v) => updateForm((f) => ({ ...f, wecom_enabled: v }))}
          />
          <Field label="Webhook">
            <Input
              value={form.wecom_webhook}
              onChange={(e) =>
                updateForm((f) => ({ ...f, wecom_webhook: e.target.value }))
              }
              placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
            />
          </Field>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div
              className={`text-xs ${wecomError ? "text-[var(--color-danger-text)]" : "text-[var(--color-text-muted)]"}`}
            >
              {wecomStatus}
            </div>
            <Button
              variant="secondary"
              type="button"
              onClick={testWecom}
              loading={wecomTesting}
            >
              测试企业微信
            </Button>
          </div>
        </div>
      </Panel>

      <Panel title="邮箱" subtitle="SMTP 变化提醒">
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <SwitchRow
              label="启用邮箱推送"
              checked={form.email_enabled}
              onChange={(v) => updateForm((f) => ({ ...f, email_enabled: v }))}
            />
            <SwitchRow
              label="使用 SSL"
              checked={form.smtp_use_ssl}
              onChange={(v) => updateForm((f) => ({ ...f, smtp_use_ssl: v }))}
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="SMTP 服务器">
              <Input
                value={form.smtp_host}
                onChange={(e) =>
                  updateForm((f) => ({ ...f, smtp_host: e.target.value }))
                }
                placeholder="smtp.example.com"
              />
            </Field>
            <Field label="端口">
              <Input
                type="number"
                min={1}
                value={form.smtp_port}
                onChange={(e) =>
                  updateForm((f) => ({
                    ...f,
                    smtp_port: Number(e.target.value || 465),
                  }))
                }
              />
            </Field>
            <Field label="邮箱账号">
              <Input
                value={form.smtp_username}
                onChange={(e) =>
                  updateForm((f) => ({ ...f, smtp_username: e.target.value }))
                }
              />
            </Field>
            <Field label="邮箱授权码或密码" help="编辑时留空表示不修改">
              <Input
                type="password"
                value={form.smtp_password}
                onChange={(e) =>
                  updateForm((f) => ({ ...f, smtp_password: e.target.value }))
                }
              />
            </Field>
            <Field label="发件人">
              <Input
                value={form.smtp_from}
                onChange={(e) =>
                  updateForm((f) => ({ ...f, smtp_from: e.target.value }))
                }
                placeholder="默认使用邮箱账号"
              />
            </Field>
            <Field label="收件人" help="多个邮箱用逗号分隔">
              <Input
                value={form.smtp_to}
                onChange={(e) =>
                  updateForm((f) => ({ ...f, smtp_to: e.target.value }))
                }
              />
            </Field>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div
              className={`text-xs ${emailError ? "text-[var(--color-danger-text)]" : "text-[var(--color-text-muted)]"}`}
            >
              {emailStatus}
            </div>
            <div className="flex gap-2">
              <Button type="button" onClick={save} loading={saving}>
                保存配置
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={testEmail}
                loading={emailTesting}
              >
                测试邮件
              </Button>
            </div>
          </div>
        </div>
      </Panel>
    </div>
  );
}
