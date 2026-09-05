<script setup lang="ts">
import { onMounted, ref, shallowRef, watch } from "vue";
import PageHeader from "@/components/PageHeader.vue";
import Panel from "@/components/Panel.vue";
import Badge from "@/components/Badge.vue";
import { Button, Field, Input, SwitchRow } from "@/components/ui";
import { errorText, useToast } from "@/composables/useToast";
import { useConsoleData } from "@/composables/useConsoleData";
import { api } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { NotificationLog, NotificationSettings } from "@/lib/types";

type BadgeTone = "success" | "warning" | "danger" | "neutral";

function channelLabel(channel?: string): string {
  if (channel === "email") return "邮箱";
  if (channel === "wecom") return "企业微信";
  return channel || "—";
}

function logStatusTone(status?: string): BadgeTone {
  const s = String(status || "").toLowerCase();
  if (["success", "sent", "ok"].includes(s)) return "success";
  if (["failed", "error"].includes(s)) return "danger";
  if (["pending", "queued"].includes(s)) return "warning";
  return "neutral";
}

function logStatusLabel(status?: string): string {
  const s = String(status || "").toLowerCase();
  if (["success", "sent", "ok"].includes(s)) return "成功";
  if (["failed", "error"].includes(s)) return "失败";
  if (["pending", "queued"].includes(s)) return "待发送";
  return status || "—";
}

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

const { notify, refresh } = useConsoleData();
const toast = useToast();

const form = ref<NotificationForm>({ ...EMPTY_FORM });
let formDirty = false;
const wecomStatus = ref("未配置");
// GET 契约只回 wecom_webhook_masked 掩码串(P1-2),明文不回显:
// 输入框留空 = 服务端沿用旧值,已配置时 placeholder 提示更换方式
const wecomHasWebhook = ref(false);
const emailStatus = ref("未配置");
const wecomError = ref(false);
const emailError = ref(false);
const saving = ref(false);
const emailTesting = ref(false);
const wecomTesting = ref(false);
const logs = shallowRef<NotificationLog[]>([]);
const logsLoading = ref(false);

async function loadLogs() {
  logsLoading.value = true;
  try {
    const resp = await api.notificationLogs();
    logs.value = resp.data || [];
  } catch {
    logs.value = [];
  } finally {
    logsLoading.value = false;
  }
}

onMounted(() => {
  void loadLogs();
});

watch(
  notify,
  (settings: NotificationSettings | null) => {
    if (!settings) return;
    if (!formDirty) {
      form.value = {
        ...EMPTY_FORM,
        wecom_enabled: !!settings.wecom_enabled,
        email_enabled: !!settings.email_enabled,
        smtp_host: settings.smtp_host || "",
        smtp_port: settings.smtp_port || 465,
        smtp_username: settings.smtp_username || "",
        smtp_use_ssl: settings.smtp_use_ssl !== false,
        smtp_from: settings.smtp_from || "",
        smtp_to: settings.smtp_to || "",
      };
    }
    wecomHasWebhook.value = !!settings.wecom_has_webhook;
    const wecomParts = [
      settings.wecom_enabled ? "企业微信已启用" : "企业微信未启用",
    ];
    if (settings.wecom_has_webhook) {
      wecomParts.push(
        settings.wecom_webhook_masked
          ? `Webhook 已保存(${settings.wecom_webhook_masked})`
          : "Webhook 已保存",
      );
    }
    if (settings.wecom_last_sent_at) {
      wecomParts.push(`上次发送：${fmtTime(settings.wecom_last_sent_at)}`);
    }
    if (settings.wecom_last_error) {
      wecomParts.push(`错误：${settings.wecom_last_error}`);
    }
    wecomStatus.value = wecomParts.join(" · ");
    wecomError.value = !!settings.wecom_last_error;

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
    emailStatus.value = emailParts.join(" · ");
    emailError.value = !!settings.email_last_error;
  },
  { immediate: true },
);

function updateForm(updater: (previous: NotificationForm) => NotificationForm) {
  formDirty = true;
  form.value = updater(form.value);
}

function payload() {
  return {
    wecom_enabled: form.value.wecom_enabled,
    wecom_webhook: form.value.wecom_webhook.trim(),
    email_enabled: form.value.email_enabled,
    smtp_host: form.value.smtp_host.trim(),
    smtp_port: Math.max(1, Number(form.value.smtp_port || 465)),
    smtp_username: form.value.smtp_username.trim(),
    smtp_password: form.value.smtp_password,
    smtp_use_ssl: form.value.smtp_use_ssl,
    smtp_from: form.value.smtp_from.trim(),
    smtp_to: form.value.smtp_to.trim(),
  };
}

async function save() {
  saving.value = true;
  emailStatus.value = "保存中...";
  emailError.value = false;
  try {
    await api.saveNotificationSettings(payload());
    formDirty = false;
    await refresh();
    void loadLogs();
    // 成功也要落地：以前只留一句「保存中...」，看起来像卡住了
    emailStatus.value = "设置已保存";
    emailError.value = false;
    toast.success("推送设置已保存");
  } catch (err) {
    const message = errorText(err, "保存失败");
    emailStatus.value = `保存失败：${message}`;
    emailError.value = true;
    toast.error(`保存推送设置失败：${message}`);
  } finally {
    saving.value = false;
  }
}

async function testEmail() {
  emailTesting.value = true;
  emailStatus.value = "测试邮件发送中...";
  emailError.value = false;
  try {
    const res = await api.testEmail(payload());
    // 后端用 success=false 表达业务失败（HTTP 仍是 200），必须据此判定
    const message = res.message || (res.success ? "测试邮件已发送" : "测试失败");
    emailStatus.value = message;
    emailError.value = !res.success;
    if (res.success) toast.success(message);
    else toast.error(`邮件测试失败：${message}`);
    await refresh();
    void loadLogs();
  } catch (err) {
    const message = errorText(err, "测试失败");
    emailStatus.value = `测试失败：${message}`;
    emailError.value = true;
    toast.error(`邮件测试失败：${message}`);
  } finally {
    emailTesting.value = false;
  }
}

async function testWecom() {
  wecomTesting.value = true;
  wecomStatus.value = "测试企业微信发送中...";
  wecomError.value = false;
  try {
    const res = await api.testWecom(payload());
    const message = res.message || (res.success ? "测试消息已发送" : "测试失败");
    wecomStatus.value = message;
    wecomError.value = !res.success;
    if (res.success) toast.success(message);
    else toast.error(`企业微信测试失败：${message}`);
    await refresh();
    void loadLogs();
  } catch (err) {
    const message = errorText(err, "测试失败");
    wecomStatus.value = `测试失败：${message}`;
    wecomError.value = true;
    toast.error(`企业微信测试失败：${message}`);
  } finally {
    wecomTesting.value = false;
  }
}
</script>

<template>
  <div class="upstream-rise flex flex-col gap-6 md:gap-8">
    <PageHeader
      title="消息推送"
      subtitle="支持企业微信和邮箱推送，检测到倍率或分组变化后自动发送提醒。"
    />

    <Panel title="企业微信" subtitle="群机器人 Webhook，无需服务器回调">
      <div class="flex flex-col gap-4">
        <SwitchRow
          label="启用企业微信推送"
          :checked="form.wecom_enabled"
          @update:checked="(v: boolean) => updateForm((f) => ({ ...f, wecom_enabled: v }))"
        />
        <Field label="Webhook">
          <Input
            :model-value="form.wecom_webhook"
            @update:model-value="(v: string) => updateForm((f) => ({ ...f, wecom_webhook: v }))"
            :placeholder="wecomHasWebhook
              ? '已配置，留空沿用；更换请粘贴新地址'
              : 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx'"
          />
        </Field>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div :class="['text-[12.5px]', wecomError ? 'text-danger-fg' : 'text-ink-muted']">
            {{ wecomStatus }}
          </div>
          <Button
            variant="secondary"
            type="button"
            :loading="wecomTesting"
            @click="testWecom"
          >
            测试企业微信
          </Button>
        </div>
      </div>
    </Panel>

    <Panel title="邮箱" subtitle="SMTP 变化提醒">
      <div class="flex flex-col gap-4">
        <div class="grid gap-3 sm:grid-cols-2">
          <SwitchRow
            label="启用邮箱推送"
            :checked="form.email_enabled"
            @update:checked="(v: boolean) => updateForm((f) => ({ ...f, email_enabled: v }))"
          />
          <SwitchRow
            label="使用 SSL"
            :checked="form.smtp_use_ssl"
            @update:checked="(v: boolean) => updateForm((f) => ({ ...f, smtp_use_ssl: v }))"
          />
        </div>
        <div class="grid gap-4 sm:grid-cols-2">
          <Field label="SMTP 服务器">
            <Input
              :model-value="form.smtp_host"
              @update:model-value="(v: string) => updateForm((f) => ({ ...f, smtp_host: v }))"
              placeholder="smtp.example.com"
            />
          </Field>
          <Field label="端口">
            <Input
              type="number"
              :min="1"
              :model-value="form.smtp_port"
              @update:model-value="(v: string) => updateForm((f) => ({ ...f, smtp_port: Number(v || 465) }))"
            />
          </Field>
          <Field label="邮箱账号">
            <Input
              :model-value="form.smtp_username"
              @update:model-value="(v: string) => updateForm((f) => ({ ...f, smtp_username: v }))"
            />
          </Field>
          <Field label="邮箱授权码或密码" help="编辑时留空表示不修改">
            <Input
              type="password"
              :model-value="form.smtp_password"
              @update:model-value="(v: string) => updateForm((f) => ({ ...f, smtp_password: v }))"
            />
          </Field>
          <Field label="发件人">
            <Input
              :model-value="form.smtp_from"
              @update:model-value="(v: string) => updateForm((f) => ({ ...f, smtp_from: v }))"
              placeholder="默认使用邮箱账号"
            />
          </Field>
          <Field label="收件人" help="多个邮箱用逗号分隔">
            <Input
              :model-value="form.smtp_to"
              @update:model-value="(v: string) => updateForm((f) => ({ ...f, smtp_to: v }))"
            />
          </Field>
        </div>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div :class="['text-[12.5px]', emailError ? 'text-danger-fg' : 'text-ink-muted']">
            {{ emailStatus }}
          </div>
          <div class="flex gap-2">
            <Button type="button" :loading="saving" @click="save">
              保存配置
            </Button>
            <Button
              type="button"
              variant="secondary"
              :loading="emailTesting"
              @click="testEmail"
            >
              测试邮件
            </Button>
          </div>
        </div>
      </div>
    </Panel>

    <Panel
      title="推送日志"
      :subtitle="`${logs.length} 条记录`"
    >
      <template #action>
        <Button
          variant="secondary"
          size="sm"
          :loading="logsLoading"
          @click="loadLogs"
        >
          刷新
        </Button>
      </template>

      <div v-if="logs.length" class="priceai-scrollbar overflow-x-auto pb-1">
        <table class="w-full min-w-max table-auto text-left text-[13px]">
          <thead>
            <tr class="border-b border-line-soft text-[11.5px] font-semibold tracking-[0.04em] text-ink-soft uppercase">
              <th class="pb-2.5 pr-3">时间</th>
              <th class="pb-2.5 pr-3">通道</th>
              <th class="pb-2.5 pr-3">状态</th>
              <th class="pb-2.5 pr-3">收件人</th>
              <th class="pb-2.5 pr-3">消息</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="log in logs"
              :key="log.id || log.created_at"
              class="border-b border-line-soft transition-colors duration-[var(--motion-fast)] last:border-0 hover:bg-sunken-hover"
            >
              <td class="py-2.5 pr-3 text-ink-soft tabular">
                {{ fmtTime(log.created_at) }}
              </td>
              <td class="py-2.5 pr-3">
                <Badge tone="neutral">{{ channelLabel(log.channel) }}</Badge>
              </td>
              <td class="py-2.5 pr-3">
                <Badge :tone="logStatusTone(log.status)" dot>
                  {{ logStatusLabel(log.status) }}
                </Badge>
              </td>
              <td class="py-2.5 pr-3 text-ink-muted">
                {{ log.target || "—" }}
              </td>
              <td class="py-2.5 pr-3 text-[12px] text-ink-muted">
                {{ log.error_message || log.message || "—" }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="text-[13px] text-ink-muted">
        {{ logsLoading ? "加载中..." : "暂无推送记录" }}
      </div>
    </Panel>
  </div>
</template>
