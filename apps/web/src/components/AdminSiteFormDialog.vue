<script setup lang="ts">
import { computed, ref, shallowRef, watch } from "vue";
import { api } from "@/lib/api";
import { errorText, useToast } from "@/composables/useToast";
import { fmtTime } from "@/lib/format";
import type { AdminSite, AdminSiteFormPayload } from "@/lib/types";
import { Button, Field, Input, Modal, Select, SwitchRow } from "@/components/ui";

interface Props {
  open: boolean;
  site: AdminSite | null;
  onSaved: () => Promise<void> | void;
}
const props = defineProps<Props>();
const emit = defineEmits<{ close: [] }>();

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
    access_token: form.platform === "newapi" ? form.access_token.trim() : "",
    access_user_id: form.platform === "newapi" ? form.access_user_id.trim() : "",
    login_username: form.login_username.trim(),
    login_password: form.login_password,
    key_sync_enabled: form.platform === "newapi" && form.key_sync_enabled,
    key_sync_interval_minutes: Math.max(5, Math.min(1440, Number(form.key_sync_interval_minutes) || 5)),
  };
}

const toast = useToast();
const editing = computed(() => !!props.site);
const form = ref<AdminSiteFormPayload>({ ...empty });
const msg = ref("");
const busy = ref(false);
const testing = ref(false);
const liveSite = shallowRef<AdminSite | null>(props.site);

watch(
  () => [props.open, props.site?.id] as const,
  () => {
    if (!props.open) return;
    liveSite.value = props.site;
    msg.value = "";
    form.value = props.site
      ? {
          platform: props.site.platform || "newapi",
          name: props.site.name,
          base_url: props.site.base_url,
          access_token: "",
          access_user_id: props.site.access_user_id || "",
          login_username: props.site.login_username || "",
          login_password: "",
          key_sync_enabled: !!props.site.key_sync_enabled,
          key_sync_interval_minutes: props.site.key_sync_interval_minutes || 5,
        }
      : { ...empty };
  },
  { immediate: true },
);

watch(
  () => [props.open, props.site?.id] as const,
  (_new, _old, onCleanup) => {
    if (!props.open || !props.site?.id) return;
    const siteId = props.site.id;
    let cancelled = false;
    const refreshStatus = async () => {
      try {
        const response = await api.adminSites();
        const latest = response.data?.find((item) => item.id === siteId) || null;
        if (!cancelled && latest) liveSite.value = latest;
      } catch {
        // 状态轮询失败不应打断正在编辑的表单。
      }
    };
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), 10_000);
    onCleanup(() => {
      cancelled = true;
      window.clearInterval(timer);
    });
  },
  { immediate: true },
);

function set<K extends keyof AdminSiteFormPayload>(key: K, value: AdminSiteFormPayload[K]) {
  form.value = { ...form.value, [key]: value };
}

function onPlatformChange(value: string) {
  const platform = value as AdminSiteFormPayload["platform"];
  form.value = {
    ...form.value,
    platform,
    access_token: "",
    access_user_id: "",
    login_username: "",
    login_password: "",
    key_sync_enabled: false,
    key_sync_interval_minutes: 5,
  };
}

const keySyncIntervalModel = computed<string>({
  get: () => String(form.value.key_sync_interval_minutes),
  set: (v: string) => set("key_sync_interval_minutes", Number(v)),
});

function validateCredentials(payload: AdminSiteFormPayload): string {
  if (payload.platform === "newapi") {
    const hasToken = Boolean(payload.access_token || props.site?.has_access_token);
    if (!hasToken || !payload.access_user_id) return "请填写系统访问令牌和 NewAPI 用户 ID";
    return "";
  }
  const hasPassword = Boolean(payload.login_password || props.site?.has_login_password);
  if (!payload.login_username || !hasPassword) return "请填写 sub2api 管理员邮箱和密码";
  return "";
}

async function testConnection() {
  const payload = normalizedPayload(form.value);
  if (!payload.base_url) { msg.value = "请填写 Base URL"; return; }
  const validationError = validateCredentials(payload);
  if (validationError) { msg.value = validationError; return; }
  testing.value = true;
  msg.value = "检测中...";
  try {
    const result = await api.testAdminSite({ ...payload, admin_site_id: props.site?.id });
    const count = form.value.platform === "sub2api"
      ? `${result.channels_count ?? 0} 个渠道`
      : `${result.groups_count ?? 0} 个分组`;
    const text = `连接成功：可见 ${count}`;
    msg.value = text;
    toast.success(text);
  } catch (error) {
    const message = errorText(error, "连接失败");
    msg.value = `失败：${message}`;
    toast.error(`主站连接失败：${message}`);
  } finally {
    testing.value = false;
  }
}

async function persistAdminSite(payload: AdminSiteFormPayload): Promise<void> {
  if (props.site?.id) {
    await api.updateAdminSite(props.site.id, payload);
    await props.onSaved();
    return;
  }
  await api.createAdminSite(payload);
  await props.onSaved();
}

function validatedPayload(): AdminSiteFormPayload | null {
  const payload = normalizedPayload(form.value);
  if (!payload.name || !payload.base_url) { msg.value = "请填写名称和 Base URL"; return null; }
  const validationError = validateCredentials(payload);
  if (validationError) { msg.value = validationError; return null; }
  return payload;
}

async function save() {
  const payload = validatedPayload();
  if (!payload) return;
  busy.value = true;
  msg.value = "";
  try {
    await persistAdminSite(payload);
    toast.success(props.site ? `主站「${payload.name}」已保存` : `主站「${payload.name}」已添加`);
    emit("close");
  } catch (error) {
    const message = errorText(error, "保存失败");
    msg.value = message;
    toast.error(`保存主站失败：${message}`);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <Modal :open="open" :title="editing ? '编辑主站' : '添加主站'" subtitle="统一主站入口，后端按平台读取渠道配置" @close="emit('close')">
    <div class="flex flex-col gap-4">
      <div class="grid gap-3 sm:grid-cols-2">
        <Field label="主站类型">
          <Select :model-value="form.platform" :disabled="editing" @update:model-value="onPlatformChange">
            <option value="newapi">NewAPI</option>
            <option value="sub2api">sub2api</option>
          </Select>
        </Field>
        <Field label="名称">
          <Input v-model="form.name" maxlength="80" placeholder="例如 我的主站" />
        </Field>
      </div>

      <Field :label="`${form.platform === 'sub2api' ? 'sub2api' : 'NewAPI'} Base URL`" help="填写站点根地址，不带具体 API 路径">
        <Input v-model="form.base_url" placeholder="https://example.com" />
      </Field>

      <template v-if="form.platform === 'newapi'">
        <div class="grid gap-3 border-t border-line-soft pt-4 sm:grid-cols-2">
          <Field label="管理员系统访问令牌" :help="site?.has_access_token ? '已保存，留空保持不变' : '使用具备渠道读取权限的管理员令牌'">
            <Input v-model="form.access_token" type="password" autocomplete="off" :placeholder="site?.has_access_token ? '已保存' : '系统访问令牌'" />
          </Field>
          <Field label="NewAPI 用户 ID (New-Api-User)">
            <Input v-model="form.access_user_id" placeholder="例如 1" />
          </Field>
        </div>

        <div class="grid gap-3 border-t border-line-soft pt-4 sm:grid-cols-2">
          <Field label="主站网页登录账号" help="用于读取受保护的渠道 key">
            <Input v-model="form.login_username" autocomplete="username" />
          </Field>
          <Field label="主站网页登录密码" :help="site?.has_login_password ? '已保存，留空保持不变' : undefined">
            <Input v-model="form.login_password" type="password" autocomplete="current-password" :placeholder="site?.has_login_password ? '已保存' : '登录密码'" />
          </Field>
        </div>

        <div class="flex flex-col gap-3 border-t border-line-soft pt-4">
          <SwitchRow label="自动更新渠道 key" v-model:checked="form.key_sync_enabled" />
          <Field v-if="form.key_sync_enabled" label="每次更新间隔（分钟）" help="每次只更新 fetched_at 最旧的一个渠道；允许 5–1440 分钟">
            <Input v-model="keySyncIntervalModel" type="number" min="5" max="1440" step="1" />
          </Field>
          <div v-if="liveSite?.key_sync_enabled" class="rounded-[var(--radius-md)] border border-line bg-panel-soft px-3 py-2 text-[12px] leading-relaxed text-ink-muted" aria-live="polite">
            <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span :class="['inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold', liveSite.key_sync_last_error ? 'bg-danger-bg text-danger-fg' : liveSite.key_sync_last_at ? 'bg-success-bg text-success-fg' : 'bg-sunken text-ink-muted']">
                {{ liveSite.key_sync_last_error ? '更新失败' : liveSite.key_sync_last_at ? '更新成功' : '等待首次尝试' }}
              </span>
              <span class="tabular-nums">
                最近尝试：{{ fmtTime(liveSite.key_sync_last_at) }} · 下次尝试：{{ fmtTime(liveSite.key_sync_next_at) }}
              </span>
            </div>
            <div v-if="liveSite.key_sync_last_error" class="mt-1 text-danger-fg">
              失败原因：{{ liveSite.key_sync_last_error }}{{ liveSite.key_sync_backoff_until ? ` · 暂停至 ${fmtTime(liveSite.key_sync_backoff_until)}` : '' }}
            </div>
          </div>
        </div>
      </template>

      <div v-else class="grid gap-3 border-t border-line-soft pt-4 sm:grid-cols-2">
        <Field label="sub2api 管理员邮箱">
          <Input v-model="form.login_username" type="email" autocomplete="username" placeholder="admin@example.com" />
        </Field>
        <Field label="sub2api 管理员密码" :help="site?.has_login_password ? '已保存，留空保持不变' : undefined">
          <Input v-model="form.login_password" type="password" autocomplete="current-password" :placeholder="site?.has_login_password ? '已保存' : '管理员密码'" />
        </Field>
        <div v-if="site?.has_sub2api_session" class="text-[12.5px] text-success-fg sm:col-span-2">
          管理员登录态可用
        </div>
        <div v-else-if="site?.login_last_error" class="text-[12.5px] text-danger-fg sm:col-span-2">
          最近登录失败：{{ site.login_last_error }}
        </div>
      </div>

      <div class="flex flex-wrap gap-2 border-t border-line-soft pt-4">
        <Button variant="secondary" :loading="testing" :disabled="busy" @click="testConnection">
          测试连接
        </Button>
        <Button :loading="busy" :disabled="testing" @click="save">
          保存
        </Button>
      </div>
      <div v-if="msg" class="rounded-[var(--radius-md)] bg-sunken px-3 py-2 text-[12.5px] text-ink-muted">
        {{ msg }}
      </div>
    </div>
  </Modal>
</template>
