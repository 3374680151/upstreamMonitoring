<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { api } from "@/lib/api";
import {
  probeSessionBridge,
  syncSiteBrowserSession,
} from "@/lib/browserSessionBridge";
import { errorText, useToast } from "@/composables/useToast";
import ChannelDiscoveryPanel from "@/components/ChannelDiscoveryPanel.vue";
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
  login_enabled: true,
  auth_mode: "browser",
  login_username: "",
  login_password: "",
  access_token: "",
  refresh_token: "",
  token_expires_at: "",
  access_user_id: "",
  enabled: true,
  system_token_fallback_enabled: false,
};

const props = defineProps<{ open: boolean; site: Site | null }>();
const emit = defineEmits<{
  "update:open": [value: boolean];
  saved: [];
  "edit-site": [siteId: number];
}>();

const toast = useToast();
const form = ref<SiteFormPayload>({ ...empty });
const msg = ref("");
const busy = ref(false);
const testing = ref(false);
const authTesting = ref(false);
const savedSiteId = ref<number | null>(null);
const syncResult = ref<SiteSessionSyncState | null>(null);
const mode = ref<"manual" | "discovery">("manual");
const twoFactorCode = ref("");
const needsTwoFactor = ref(false);

watch(
  () => [props.open, props.site?.id] as const,
  () => {
    if (!props.open) return;
    msg.value = "";
    savedSiteId.value = props.site?.id ?? null;
    syncResult.value = null;
    twoFactorCode.value = "";
    needsTwoFactor.value = false;
    mode.value = "manual";
    if (props.site) {
      form.value = {
        ...empty,
        name: props.site.name,
        platform: (props.site.platform as Platform) || "newapi",
        base_url: props.site.base_url,
        interval_minutes: props.site.interval_minutes || 3,
        login_enabled: !!props.site.login_enabled,
        // 手动导入登录态（token）已废弃：旧数据回填时直接落到浏览器自动同步
        auth_mode: (
          props.site.auth_mode === "token" ? "browser" : props.site.auth_mode
        ) as AuthMode,
        login_username: props.site.login_username || "",
        access_user_id: props.site.access_user_id || "",
        token_expires_at: props.site.token_expires_at || "",
        enabled: !!props.site.enabled,
        system_token_fallback_enabled:
          !!props.site.system_token_fallback_enabled,
      };
    } else {
      form.value = { ...empty };
    }
  },
  { immediate: true },
);

function close() {
  emit("update:open", false);
}

function set<K extends keyof SiteFormPayload>(key: K, value: SiteFormPayload[K]) {
  form.value = { ...form.value, [key]: value };
}

function setPlatform(platform: Platform) {
  syncResult.value = null;
  form.value =
    platform === "sub2api"
      ? {
          ...form.value,
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
          ...form.value,
          platform,
          login_enabled: true,
          auth_mode: "browser",
          login_username: "",
          login_password: "",
          access_token: "",
          refresh_token: "",
          token_expires_at: "",
          access_user_id: "",
        };
}

function onToggleLoginEnabled(loginEnabled: boolean) {
  form.value = {
    ...form.value,
    login_enabled: loginEnabled,
    auth_mode: loginEnabled ? form.value.auth_mode : "token",
  };
}

function onAuthModeChange(value: AuthMode) {
  set("auth_mode", value);
  needsTwoFactor.value = false;
  twoFactorCode.value = "";
}

function onModeChange(value: string) {
  mode.value = value as "manual" | "discovery";
}

function closeDiscovery() {
  mode.value = "manual";
}

const isSub2api = computed(() => form.value.platform === "sub2api");
const tokenMode = computed(() => form.value.auth_mode === "token");
const browserMode = computed(() => form.value.auth_mode === "browser");
const passwordMode = computed(() => form.value.auth_mode === "password");
const newApiPasswordMode = computed(() => !isSub2api.value && passwordMode.value);
const systemTokenFallbackVisible = computed(
  () => !isSub2api.value && (browserMode.value || passwordMode.value),
);
const systemTokenFallbackHelp = computed(() => {
  const state = props.site?.has_system_access_token
    ? "当前已存兜底令牌"
    : "尚未生成兜底令牌";
  return `开启后，登录态同步（或密码登录）成功且尚未生成时会自动向上游生成系统访问令牌并保存；浏览器会话失效时用它继续读余额与分组。当前：${state}。上游每次生成都会作废旧的系统访问令牌，如需轮换请在渠道详情页手动重新生成。`;
});
const sameSavedPlatform = computed(() =>
  Boolean(props.site && props.site.platform === form.value.platform),
);
const sameSavedAuthMode = computed(() =>
  Boolean(
    sameSavedPlatform.value &&
      (props.site?.auth_mode || "token") === form.value.auth_mode,
  ),
);
const hasSavedNewApiToken = computed(() =>
  Boolean(
    sameSavedAuthMode.value &&
      !isSub2api.value &&
      tokenMode.value &&
      props.site?.has_access_token,
  ),
);
const hasSavedNewApiPassword = computed(() =>
  Boolean(
    sameSavedAuthMode.value &&
      !isSub2api.value &&
      passwordMode.value &&
      props.site?.has_login_password,
  ),
);
const hasSavedSub2ApiToken = computed(() =>
  Boolean(
    sameSavedAuthMode.value &&
      isSub2api.value &&
      tokenMode.value &&
      props.site?.has_access_token,
  ),
);
const hasSavedSub2ApiPassword = computed(() =>
  Boolean(
    sameSavedPlatform.value &&
      isSub2api.value &&
      (passwordMode.value || browserMode.value) &&
      props.site?.has_login_password,
  ),
);
const savedTokenHelp = computed(() =>
  props.site && (hasSavedNewApiToken.value || hasSavedSub2ApiToken.value)
    ? "当前已有令牌，留空保持不变；填写新值会替换原令牌"
    : "仅用于读取上游数据，不需要管理员权限",
);
const savedPasswordHelp = computed(() =>
  hasSavedSub2ApiPassword.value || hasSavedNewApiPassword.value
    ? "当前已有密码，留空保持不变；填写新值会替换原密码"
    : "尚未配置，填写后启用账号密码登录",
);

async function runBrowserSync(targetSiteId: number): Promise<SiteSessionSyncState> {
  msg.value = "正在查找浏览器登录态";
  syncResult.value = null;
  return syncSiteBrowserSession(targetSiteId);
}

async function runNewApiPasswordLogin(targetSiteId: number): Promise<boolean> {
  msg.value = "正在验证 NewAPI 用户名密码";
  const result = await api.loginNewApiSite(
    targetSiteId,
    twoFactorCode.value.trim(),
  );
  if (result.requires_2fa) {
    needsTwoFactor.value = true;
    msg.value = result.message || "需要 2FA 验证码";
    return false;
  }
  if (!result.success) {
    throw new Error(result.message || "NewAPI 用户名密码验证失败");
  }
  needsTwoFactor.value = false;
  twoFactorCode.value = "";
  const suffix = result.warning ? `；${result.warning}` : "";
  msg.value = `登录验证成功：${result.groups_count ?? 0} 个分组${suffix}`;
  return true;
}

async function save() {
  busy.value = true;
  msg.value = "";
  try {
    const payload: SiteFormPayload = {
      ...form.value,
      login_enabled:
        isSub2api.value ||
        form.value.login_enabled ||
        newApiPasswordMode.value ||
        browserMode.value,
      auth_mode: form.value.auth_mode,
    };
    let targetSiteId = props.site?.id ?? savedSiteId.value;
    if (props.site || savedSiteId.value) {
      if (!targetSiteId) throw new Error("渠道 ID 无效");
      await api.updateSite(targetSiteId, payload);
    } else {
      const created = await api.createSite(payload);
      if (!created.id) throw new Error("后端未返回新渠道 ID");
      targetSiteId = created.id;
      savedSiteId.value = created.id;
    }
    emit("saved");
    const successLabel = props.site
      ? `渠道「${payload.name}」已保存`
      : `渠道「${payload.name}」已添加`;

    if (browserMode.value) {
      const result = await runBrowserSync(targetSiteId);
      syncResult.value = result;
      if (result.status === "ready") {
        toast.success(`${successLabel}，登录态已同步`);
      } else {
        const message = result.message || result.error_code || "登录态同步未完成";
        toast.info(`${successLabel}：${message}；可稍后在列表中点击「同步登录态」重试`);
      }
      close();
      return;
    }
    if (newApiPasswordMode.value) {
      const loggedIn = await runNewApiPasswordLogin(targetSiteId);
      if (!loggedIn) {
        toast.info(`${successLabel}：请输入 2FA 验证码后再次点击保存`);
        return;
      }
    }
    toast.success(successLabel);
    close();
  } catch (err) {
    const message = errorText(err, "保存失败");
    msg.value = message;
    toast.error(`保存渠道失败：${message}`);
  } finally {
    busy.value = false;
  }
}

async function testBrowserBridge() {
  testing.value = true;
  try {
    const available = await probeSessionBridge();
    msg.value = available
      ? "浏览器同步扩展已连接"
      : "浏览器同步扩展未连接或版本过旧，请重新加载桌面项目中的 0.1.3 扩展并刷新页面";
  } finally {
    testing.value = false;
  }
}

async function testConnection() {
  const baseUrl = form.value.base_url.trim().replace(/\/+$/, "");
  if (!baseUrl) {
    msg.value = "请先填写 Base URL";
    return;
  }
  if (
    isSub2api.value &&
    passwordMode.value &&
    (!form.value.login_username.trim() || !form.value.login_password)
  ) {
    msg.value = "请填写 sub2api 用户邮箱和密码";
    return;
  }
  if (isSub2api.value && tokenMode.value && !form.value.access_token.trim()) {
    msg.value = "请填写 sub2api auth_token";
    return;
  }
  testing.value = true;
  msg.value = "检测中...";
  try {
    const res = await api.checkConnection({
      platform: form.value.platform,
      base_url: baseUrl,
      auth_mode: form.value.auth_mode,
      login_username: form.value.login_username.trim(),
      login_password: form.value.login_password,
      access_token: form.value.access_token.trim(),
      refresh_token: form.value.refresh_token.trim(),
    });
    if (res.success) {
      const text = `连接成功：${res.groups_count ?? 0} 个分组`;
      msg.value = text;
      toast.success(text);
    } else {
      const text = res.message || "连接失败";
      msg.value = `失败：${text}`;
      toast.error(`连接失败：${text}`);
    }
  } catch (err) {
    const message = errorText(err, "连接失败");
    msg.value = `失败：${message}`;
    toast.error(`连接失败：${message}`);
  } finally {
    testing.value = false;
  }
}

async function testAuth() {
  const baseUrl = form.value.base_url.trim().replace(/\/+$/, "");
  if (browserMode.value) return testBrowserBridge();
  if (isSub2api.value) {
    return testConnection();
  }
  if (
    newApiPasswordMode.value &&
    (!baseUrl || !form.value.login_username.trim() || !form.value.login_password)
  ) {
    msg.value = "请填写 Base URL、NewAPI 用户名和密码";
    return;
  }
  if (
    !newApiPasswordMode.value &&
    (!baseUrl ||
      !form.value.access_token.trim() ||
      !form.value.access_user_id.trim())
  ) {
    msg.value = "请填写 Base URL、系统访问令牌和 NewAPI 用户 ID";
    return;
  }
  authTesting.value = true;
  msg.value = newApiPasswordMode.value
    ? "用户名密码验证中..."
    : "访问令牌测试中...";
  try {
    const res = await api.checkLogin({
      base_url: baseUrl,
      auth_mode: form.value.auth_mode,
      login_username: form.value.login_username.trim(),
      login_password: form.value.login_password,
      two_factor_code: twoFactorCode.value.trim(),
      access_token: form.value.access_token.trim(),
      access_user_id: form.value.access_user_id.trim(),
    });
    if (res.requires_2fa) {
      needsTwoFactor.value = true;
      msg.value = res.message || "需要 2FA 验证码";
      return;
    }
    if (res.success) {
      const text = `验证成功：认证后可见 ${res.groups_count ?? 0} 个分组`;
      msg.value = text;
      toast.success(text);
    } else {
      const text = res.message || "验证失败";
      msg.value = `验证失败：${text}`;
      toast.error(
        `${newApiPasswordMode.value ? "用户名密码" : "令牌"}验证失败：${text}`,
      );
    }
  } catch (err) {
    const message = errorText(err, "验证失败");
    msg.value = `验证失败：${message}`;
    toast.error(
      `${newApiPasswordMode.value ? "用户名密码" : "令牌"}验证失败：${message}`,
    );
  } finally {
    authTesting.value = false;
  }
}
</script>

<template>
  <Modal
    :open="open"
    :title="site ? '编辑渠道' : '添加渠道'"
    subtitle="配置 NewAPI / sub2api 上游渠道与认证方式"
    :wide="!site && mode === 'discovery'"
    @close="close"
  >
    <div v-if="!site" class="mb-4">
      <Tabs
        label="添加渠道方式"
        :items="[
          { id: 'manual', label: '手动添加' },
          { id: 'discovery', label: '从主站发现' },
        ]"
        :model-value="mode"
        @update:model-value="onModeChange"
      />
    </div>
    <ChannelDiscoveryPanel
      v-if="!site && mode === 'discovery'"
      :open="true"
      @close="closeDiscovery"
      @imported="emit('saved')"
      @edit-site="emit('edit-site', $event)"
    />
    <div v-else class="space-y-3">
      <Field label="渠道名称">
        <Input v-model="form.name" maxlength="80" />
      </Field>
      <Field label="平台类型">
        <Select
          :model-value="form.platform"
          @update:model-value="setPlatform"
        >
          <option value="newapi">NewAPI</option>
          <option value="sub2api">sub2api</option>
        </Select>
      </Field>
      <Field label="Base URL" help="例如 https://example.com，不要带具体 API 路径">
        <Input v-model="form.base_url" placeholder="https://example.com" />
      </Field>
      <Field label="监控间隔（分钟）">
        <Input
          type="number"
          :min="1"
          :model-value="form.interval_minutes"
          @update:model-value="set('interval_minutes', Math.max(1, Number($event || 3)))"
        />
      </Field>

      <div
        v-if="!isSub2api"
        class="space-y-3 rounded-2xl border border-line bg-panel-soft p-3"
      >
        <SwitchRow
          label="认证增强监控"
          :checked="form.login_enabled"
          @update:checked="onToggleLoginEnabled"
        />
        <p class="text-[11px] text-ink-soft">
          填写<b>普通用户</b>的系统访问令牌和 NewAPI 用户 ID 后，可查看隐藏/专属分组与账户额度。
          这些接口（<code>/api/user/self</code>、<code>/api/user/self/groups</code>）只要普通用户权限，
          <b>不要填管理员令牌</b>——这是别人家的上游，令牌泄露会连带暴露渠道/用户/日志管理权限。
          主站监控中的真实渠道会按 Base URL 自动匹配并复用这里的登录态，用于读取该上游账号的分组和倍率。
          真实主站渠道的新增、删除和其他配置请在主站后台完成。
        </p>
        <div v-if="form.login_enabled" class="space-y-3">
          <Field label="认证方式">
            <Select
              :model-value="form.auth_mode"
              @update:model-value="onAuthModeChange"
            >
              <option value="browser">浏览器登录态同步</option>
              <option value="token">手动系统访问令牌</option>
              <option value="password">用户名密码登录</option>
            </Select>
          </Field>
          <template v-if="systemTokenFallbackVisible">
            <SwitchRow
              label="会话失效时用系统访问令牌兜底"
              :checked="form.system_token_fallback_enabled"
              @update:checked="set('system_token_fallback_enabled', $event)"
            />
            <p class="text-[11px] leading-relaxed text-ink-soft">
              {{ systemTokenFallbackHelp }}
            </p>
          </template>
          <template v-if="browserMode">
            <div
              class="rounded-[var(--radius-md)] border border-line bg-success-bg px-3 py-2.5 text-[12.5px] text-success-fg"
            >
              保存后立即同步当前 Chrome 登录态；如失败可稍后在渠道列表中点击「同步登录态」重试。
            </div>
          </template>
          <template v-else-if="tokenMode">
            <Field
              label="系统访问令牌（普通用户即可）"
              :help="hasSavedNewApiToken ? savedTokenHelp : '尚未配置，填写后可读取余额与隐藏分组'"
            >
              <Input
                v-model="form.access_token"
                type="password"
                autocomplete="off"
                :placeholder="hasSavedNewApiToken ? '已保存，留空不修改' : '填写普通用户令牌'"
              />
            </Field>
            <Field label="NewAPI 用户 ID">
              <Input v-model="form.access_user_id" placeholder="例如：4" />
            </Field>
          </template>
          <template v-else-if="passwordMode">
            <Field label="NewAPI 用户名">
              <Input v-model="form.login_username" autocomplete="username" />
            </Field>
            <Field label="NewAPI 密码" :help="savedPasswordHelp">
              <Input
                v-model="form.login_password"
                type="password"
                autocomplete="current-password"
                :placeholder="hasSavedNewApiPassword ? '已保存，留空不修改' : '填写用户密码'"
              />
            </Field>
            <Field
              v-if="needsTwoFactor"
              label="2FA 验证码"
              help="验证码仅用于本次登录，不会保存"
            >
              <Input
                v-model="twoFactorCode"
                inputmode="numeric"
                autocomplete="one-time-code"
                placeholder="输入当前动态验证码"
              />
            </Field>
          </template>
        </div>
      </div>
      <div v-else class="space-y-3 rounded-2xl border border-line bg-panel-soft p-3">
        <p class="text-[11px] text-ink-soft">
          sub2api 默认从当前 Chrome 同步已经验证过的人机验证登录态；账号密码留作兜底。
        </p>
        <Field label="认证方式">
          <Select
            :model-value="form.auth_mode"
            @update:model-value="set('auth_mode', $event)"
          >
            <option value="browser">浏览器自动同步（推荐）</option>
            <option value="password">账号密码登录</option>
          </Select>
        </Field>
        <template v-if="browserMode">
          <div
            class="rounded-[var(--radius-md)] border border-line bg-success-bg px-3 py-2.5 text-[12.5px] text-success-fg"
          >
            浏览器登录态 → refresh_token → 账号密码
            <span
              v-if="site?.session_synced_at"
              class="mt-1 block text-[11px] opacity-80"
            >
              最近同步：{{ site.session_synced_at }}
            </span>
          </div>
          <div class="grid gap-3 sm:grid-cols-2">
            <Field label="兜底用户邮箱（可选）">
              <Input v-model="form.login_username" placeholder="user@example.com" />
            </Field>
            <Field label="兜底用户密码（可选）" :help="savedPasswordHelp">
              <Input
                v-model="form.login_password"
                type="password"
                :placeholder="hasSavedSub2ApiPassword ? '已保存，留空不修改' : '未配置'"
                autocomplete="new-password"
              />
            </Field>
          </div>
        </template>
        <template v-else-if="passwordMode">
          <Field label="用户邮箱">
            <Input v-model="form.login_username" placeholder="user@example.com" />
          </Field>
          <Field label="用户密码" :help="savedPasswordHelp">
            <Input
              v-model="form.login_password"
              type="password"
              :placeholder="hasSavedSub2ApiPassword ? '已保存，留空不修改' : '填写用户密码'"
            />
          </Field>
        </template>
      </div>

      <SwitchRow
        label="启用监控"
        :checked="form.enabled"
        @update:checked="set('enabled', $event)"
      />

      <div class="flex flex-wrap gap-2 pt-1">
        <template v-if="browserMode">
          <Button
            variant="secondary"
            class="h-8"
            :loading="testing"
            :disabled="busy"
            @click="testBrowserBridge"
          >
            检测同步扩展
          </Button>
        </template>
        <template v-else>
          <Button
            variant="secondary"
            :loading="testing"
            :disabled="busy || authTesting"
            @click="testConnection"
          >
            测试连接
          </Button>
          <Button
            variant="secondary"
            :loading="authTesting"
            :disabled="busy || testing"
            @click="testAuth"
          >
            {{ isSub2api ? "测试登录" : "测试认证" }}
          </Button>
        </template>
        <Button
          class="h-8"
          :loading="busy"
          :disabled="testing || authTesting"
          @click="save"
        >
          保存
        </Button>
      </div>
      <div
        v-if="msg"
        class="rounded-xl bg-sunken px-3 py-2 text-[12.5px] text-ink-muted"
      >
        {{ msg }}
      </div>
    </div>
  </Modal>
</template>
