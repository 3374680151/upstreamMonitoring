<script setup lang="ts">
import { computed, shallowRef, ref, watch } from "vue";
import type {
  AuthMode,
  Channel,
  ChannelUpstreamBinding,
  ChannelUpstreamBindingPayload,
  Platform,
} from "@/lib/types";
import { Button, Field, Input, Modal, Select, Textarea } from "@/components/ui";

/** 官方常见渠道类型子集（type 值透传给 NewAPI，未列出的可用「自定义」手填） */
const CHANNEL_TYPES: Array<{ value: number; label: string }> = [
  { value: 1, label: "OpenAI" },
  { value: 3, label: "Azure OpenAI" },
  { value: 14, label: "Anthropic Claude" },
  { value: 24, label: "Google Gemini" },
  { value: 33, label: "AWS Bedrock" },
  { value: 8, label: "自定义 (OpenAI 兼容)" },
];

type FormState = {
  name: string;
  type: number;
  key: string;
  base_url: string;
  models: string;
  group: string;
  weight: number;
  priority: number;
  status: number;
  model_mapping: string;
  tag: string;
  test_model: string;
  auto_ban: number;
  upstream_base_url: string;
  upstream_platform: Platform;
  upstream_auth_mode: AuthMode;
  upstream_login_username: string;
  upstream_login_password: string;
  upstream_access_token: string;
  upstream_access_user_id: string;
  upstream_refresh_token: string;
};

/** model_mapping 可能是对象或字符串，统一转成可编辑的 JSON 文本 */
function mappingToText(raw: unknown): string {
  if (raw === undefined || raw === null || raw === "") return "";
  if (typeof raw === "string") return raw;
  try {
    return JSON.stringify(raw, null, 2);
  } catch {
    return "";
  }
}

function toForm(channel: Channel | null, binding?: ChannelUpstreamBinding): FormState {
  return {
    name: String(channel?.name ?? ""),
    type: Number(channel?.type ?? 1),
    key: "",
    base_url: String(channel?.base_url ?? ""),
    models: String(channel?.models ?? ""),
    group: String(channel?.group ?? "default"),
    weight: Number(channel?.weight ?? 1),
    priority: Number(channel?.priority ?? 0),
    status: Number(channel?.status ?? 1),
    model_mapping: mappingToText(channel?.model_mapping),
    tag: String(channel?.tag ?? ""),
    test_model: String(channel?.test_model ?? ""),
    auto_ban: Number(channel?.auto_ban ?? 1),
    upstream_base_url: String(binding?.upstream_base_url ?? ""),
    upstream_platform: (binding?.upstream_platform as Platform) || "newapi",
    upstream_auth_mode: (binding?.auth_mode as AuthMode) || "token",
    upstream_login_username: "",
    upstream_login_password: "",
    upstream_access_token: "",
    upstream_access_user_id:
      !binding?.upstream_platform || binding.upstream_platform === "newapi"
        ? String(binding?.access_user_id ?? "")
        : "",
    upstream_refresh_token: "",
  };
}

interface Props {
  open: boolean;
  /** null = 新增；否则为编辑（key 字段留空表示不修改密钥） */
  channel: Channel | null;
  binding?: ChannelUpstreamBinding;
  groupNames: string[];
  onSubmit: (
    payload: Partial<Channel>,
    binding: ChannelUpstreamBindingPayload,
  ) => Promise<void>;
}
const props = defineProps<Props>();
const emit = defineEmits<{ close: [] }>();

const editing = computed(() => !!props.channel);
const form = shallowRef<FormState>(toForm(props.channel, props.binding));
const saving = ref(false);
const error = ref("");
let formDirty = false;
let formSessionId: number | null | undefined = undefined;

const bindingFormKey = computed(() =>
  props.binding
    ? [
        props.binding.upstream_base_url,
        props.binding.upstream_platform,
        props.binding.auth_mode,
        props.binding.access_user_id,
        props.binding.has_login_username,
        props.binding.has_login_password,
        props.binding.has_access_token,
        props.binding.has_refresh_token,
        props.binding.has_channel_key,
      ].join("|")
    : "",
);

const originalKeyHint = computed(() =>
  props.channel?.key
    ? `当前密钥：${props.channel.key}（掩码）`
    : props.binding?.has_channel_key
      ? "当前密钥已保存，留空保持不变"
      : "尚未配置密钥",
);

const title = computed(() =>
  editing.value
    ? `编辑渠道 · ${props.channel?.name || props.channel?.id}`
    : "添加渠道",
);

const bindingPlatform = computed(
  () => (props.binding?.upstream_platform as Platform) || "newapi",
);
const bindingAuthMode = computed(
  () => (props.binding?.auth_mode as AuthMode) || "token",
);
const sameBindingPlatform = computed(
  () => bindingPlatform.value === form.value.upstream_platform,
);
const sameBindingAuthMode = computed(
  () => sameBindingPlatform.value && bindingAuthMode.value === form.value.upstream_auth_mode,
);
const hasSavedNewApiToken = computed(
  () =>
    sameBindingPlatform.value &&
    form.value.upstream_platform === "newapi" &&
    Boolean(props.binding?.has_access_token),
);
const hasSavedNewApiPassword = computed(
  () =>
    sameBindingAuthMode.value &&
    form.value.upstream_platform === "newapi" &&
    form.value.upstream_auth_mode === "password" &&
    Boolean(props.binding?.has_login_password),
);
const hasSavedSub2ApiPassword = computed(
  () =>
    sameBindingAuthMode.value &&
    form.value.upstream_auth_mode === "password" &&
    Boolean(props.binding?.has_login_password),
);
const hasSavedSub2ApiToken = computed(
  () =>
    sameBindingAuthMode.value &&
    form.value.upstream_auth_mode === "token" &&
    Boolean(props.binding?.has_access_token),
);
const hasSavedSub2ApiRefresh = computed(
  () =>
    sameBindingAuthMode.value &&
    form.value.upstream_auth_mode === "token" &&
    Boolean(props.binding?.has_refresh_token),
);

const keyHelp = computed(() =>
  form.value.upstream_platform === "sub2api"
    ? `sub2api 必须用这个实际 key 查询所属分组和倍率；${originalKeyHint.value}`
    : editing.value
      ? `${originalKeyHint.value}。为安全起见，编辑时不回显明文密钥`
      : "新增渠道必须填写密钥",
);

watch(
  () => [props.open, props.channel?.id, bindingFormKey.value] as const,
  () => {
    if (!props.open) {
      formDirty = false;
      formSessionId = undefined;
      return;
    }
    const recordId = props.channel?.id ?? null;
    if (formSessionId === recordId && formDirty) return;
    formSessionId = recordId;
    formDirty = false;
    form.value = toForm(props.channel, props.binding);
    error.value = "";
  },
  { immediate: true },
);

function patch<K extends keyof FormState>(k: K, v: FormState[K]) {
  formDirty = true;
  form.value = { ...form.value, [k]: v };
}

async function handleSubmit() {
  if (!form.value.name.trim()) {
    error.value = "请填写渠道名称";
    return;
  }
  if (!editing.value && !form.value.key.trim()) {
    error.value = "新增渠道必须填写密钥";
    return;
  }
  // 模型重定向：允许留空；填写则必须是合法 JSON 对象（透传给官方 model_mapping）
  const mappingText = form.value.model_mapping.trim();
  if (mappingText) {
    try {
      const parsed = JSON.parse(mappingText);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        error.value = '模型重定向必须是 JSON 对象，如 {"gpt-4":"gpt-4o"}';
        return;
      }
    } catch {
      error.value = "模型重定向不是合法 JSON，请检查格式";
      return;
    }
  }
  const payload: Partial<Channel> = {
    name: form.value.name.trim(),
    type: form.value.type,
    base_url: form.value.base_url.trim(),
    models: form.value.models.trim(),
    group: form.value.group.trim() || "default",
    weight: Number(form.value.weight) || 0,
    priority: Number(form.value.priority) || 0,
    status: form.value.status,
    // 官方 model_mapping 存的是 JSON 字符串；留空表示清除重定向
    model_mapping: mappingText,
    tag: form.value.tag.trim(),
    test_model: form.value.test_model.trim(),
    auto_ban: form.value.auto_ban ? 1 : 0,
  };
  const upstreamPayload: ChannelUpstreamBindingPayload = {
    upstream_base_url: form.value.upstream_base_url.trim(),
    upstream_platform: form.value.upstream_platform,
    auth_mode: form.value.upstream_auth_mode,
    login_username: form.value.upstream_login_username.trim(),
    login_password: form.value.upstream_login_password,
    access_token: form.value.upstream_access_token.trim(),
    access_user_id: form.value.upstream_access_user_id.trim(),
    refresh_token: form.value.upstream_refresh_token.trim(),
    channel_key: form.value.key.trim(),
  };
  // 密钥：新增必填；编辑时留空 = 保持原密钥（后端 read-merge-write 不覆盖）
  if (form.value.key.trim()) payload.key = form.value.key.trim();
  saving.value = true;
  error.value = "";
  try {
    await props.onSubmit(payload, upstreamPayload);
    emit("close");
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <Modal
    :open="open"
    :title="title"
    subtitle="所有字段透传官方 /api/channel 接口，兼容官方后台配置"
    wide
    @close="emit('close')"
  >
    <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
      <Field label="渠道名称">
        <Input
          :model-value="form.name"
          @update:model-value="patch('name', $event)"
          placeholder="例如 OpenAI 主号"
        />
      </Field>
      <Field label="渠道类型">
        <Select
          :model-value="String(form.type)"
          @update:model-value="patch('type', Number($event))"
        >
          <option
            v-for="t in CHANNEL_TYPES"
            :key="t.value"
            :value="t.value"
          >
            {{ t.label }} ({{ t.value }})
          </option>
          <option
            v-if="!CHANNEL_TYPES.some((t) => t.value === form.type)"
            :value="String(form.type)"
          >
            自定义 ({{ form.type }})
          </option>
        </Select>
      </Field>
      <Field
        :label="editing ? '密钥（留空则不修改）' : '密钥'"
        :help="keyHelp"
      >
        <Input
          :model-value="form.key"
          @update:model-value="patch('key', $event)"
          :placeholder="editing ? '已保存，留空不修改' : 'sk-...'"
          autocomplete="off"
        />
      </Field>
      <Field label="Base URL（可选）" help="留空使用官方该类型默认地址">
        <Input
          :model-value="form.base_url"
          @update:model-value="patch('base_url', $event)"
          placeholder="https://api.openai.com"
        />
      </Field>
      <div class="space-y-3 rounded-2xl border border-line bg-panel-soft p-3 md:col-span-2">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div class="text-[12.5px] font-semibold text-ink-muted">上游 key 匹配</div>
            <div class="mt-0.5 text-[11px] text-ink-soft">
              优先使用本渠道单独配置的登录态；未配置时自动复用同 Base URL 的「渠道监控」登录态，按当前 key 精确匹配分组和倍率
            </div>
          </div>
          <span
            v-if="binding?.configured"
            class="rounded-full bg-success-bg px-2.5 py-0.5 text-[10px] font-semibold text-success-fg"
          >
            已配置，密钥留空不修改
          </span>
        </div>
        <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
          <Field label="上游管理地址">
            <Input
              :model-value="form.upstream_base_url"
              @update:model-value="patch('upstream_base_url', $event)"
              placeholder="https://upstream.example.com"
            />
          </Field>
          <Field label="上游平台">
            <Select
              :model-value="form.upstream_platform"
              @update:model-value="patch('upstream_platform', $event)"
            >
              <option value="newapi">NewAPI</option>
              <option value="sub2api">sub2api</option>
            </Select>
          </Field>
          <template v-if="form.upstream_platform === 'newapi'">
            <Field label="上游认证方式">
              <Select
                :model-value="form.upstream_auth_mode"
                @update:model-value="patch('upstream_auth_mode', $event)"
              >
                <option value="token">系统访问令牌</option>
                <option value="password">用户名密码</option>
              </Select>
            </Field>
            <template v-if="form.upstream_auth_mode === 'password'">
              <Field label="上游 NewAPI 用户名">
                <Input
                  :model-value="form.upstream_login_username"
                  @update:model-value="patch('upstream_login_username', $event)"
                  autocomplete="username"
                />
              </Field>
              <Field
                label="上游 NewAPI 密码"
                :help="hasSavedNewApiPassword ? '当前已保存，留空保持原密码' : '用于按当前用户读取 API 密钥分组'"
              >
                <Input
                  type="password"
                  :model-value="form.upstream_login_password"
                  @update:model-value="patch('upstream_login_password', $event)"
                  autocomplete="current-password"
                  :placeholder="hasSavedNewApiPassword ? '已保存，留空不修改' : '填写上游密码'"
                />
              </Field>
            </template>
            <template v-else>
              <Field
                label="上游普通用户系统访问令牌"
                :help="hasSavedNewApiToken ? '当前已保存，留空保持原令牌' : '尚未配置，用于读取该用户自己的 API 密钥列表，不需要管理员权限'"
              >
                <Input
                  type="password"
                  :model-value="form.upstream_access_token"
                  @update:model-value="patch('upstream_access_token', $event)"
                  autocomplete="off"
                  :placeholder="hasSavedNewApiToken ? '已保存，留空不修改' : '填写上游令牌'"
                />
              </Field>
              <Field
                label="上游用户 ID"
                :help="sameBindingPlatform && binding?.access_user_id ? '当前已配置，留空保持不变' : undefined"
              >
                <Input
                  :model-value="form.upstream_access_user_id"
                  @update:model-value="patch('upstream_access_user_id', $event)"
                  placeholder="例如：1"
                />
              </Field>
            </template>
          </template>
          <template v-else>
            <Field label="上游认证方式">
              <Select
                :model-value="form.upstream_auth_mode"
                @update:model-value="patch('upstream_auth_mode', $event)"
              >
                <option value="password">账号密码</option>
                <option value="token">导入登录态</option>
              </Select>
            </Field>
            <template v-if="form.upstream_auth_mode === 'password'">
              <Field label="上游用户邮箱">
                <Input
                  :model-value="form.upstream_login_username"
                  @update:model-value="patch('upstream_login_username', $event)"
                />
              </Field>
              <Field
                label="上游用户密码"
                :help="hasSavedSub2ApiPassword ? '当前已保存，留空保持原密码' : '尚未配置，填写后用于登录'"
              >
                <Input
                  type="password"
                  :model-value="form.upstream_login_password"
                  @update:model-value="patch('upstream_login_password', $event)"
                  autocomplete="off"
                  :placeholder="hasSavedSub2ApiPassword ? '已保存，留空不修改' : '填写上游密码'"
                />
              </Field>
            </template>
            <template v-else>
              <Field
                label="上游 auth_token"
                :help="hasSavedSub2ApiToken ? '当前已保存，留空保持原 token' : '尚未配置，填写后导入登录态'"
              >
                <Input
                  type="password"
                  :model-value="form.upstream_access_token"
                  @update:model-value="patch('upstream_access_token', $event)"
                  autocomplete="off"
                  :placeholder="hasSavedSub2ApiToken ? '已保存，留空不修改' : '填写 auth_token'"
                />
              </Field>
              <Field
                label="上游 refresh_token"
                :help="hasSavedSub2ApiRefresh ? '当前已保存，留空保持原 refresh_token' : '可选；auth_token 过期后用于自动刷新登录态'"
              >
                <Input
                  type="password"
                  :model-value="form.upstream_refresh_token"
                  @update:model-value="patch('upstream_refresh_token', $event)"
                  autocomplete="off"
                  :placeholder="hasSavedSub2ApiRefresh ? '已保存，留空不修改' : '可选'"
                />
              </Field>
            </template>
          </template>
        </div>
      </div>
      <Field label="分组（逗号分隔）">
        <Input
          :model-value="form.group"
          @update:model-value="patch('group', $event)"
          placeholder="default,vip"
        />
      </Field>
      <Field label="模型（逗号分隔，可选）">
        <Input
          :model-value="form.models"
          @update:model-value="patch('models', $event)"
          placeholder="gpt-4o,gpt-4o-mini"
        />
      </Field>
      <Field label="权重 weight" help="同优先级内按权重做负载均衡">
        <Input
          type="number"
          :min="0"
          :model-value="form.weight"
          @update:model-value="patch('weight', Number($event))"
        />
      </Field>
      <Field label="优先级 priority" help="数值越大越优先被调度">
        <Input
          type="number"
          :model-value="form.priority"
          @update:model-value="patch('priority', Number($event))"
        />
      </Field>
      <Field label="状态">
        <Select
          :model-value="String(form.status)"
          @update:model-value="patch('status', Number($event))"
        >
          <option value="1">启用</option>
          <option value="2">手动停用</option>
        </Select>
      </Field>
      <Field label="测速模型 test_model" help="留空则用渠道模型列表第一个">
        <Input
          :model-value="form.test_model"
          @update:model-value="patch('test_model', $event)"
          placeholder="gpt-4o-mini"
        />
      </Field>
      <Field label="标签 tag" help="用于分组/批量管理，可选">
        <Input
          :model-value="form.tag"
          @update:model-value="patch('tag', $event)"
          placeholder="例如 openai-主力"
        />
      </Field>
      <Field label="自动禁用 auto_ban" help="测试失败时是否自动停用该渠道">
        <Select
          :model-value="String(form.auto_ban)"
          @update:model-value="patch('auto_ban', Number($event))"
        >
          <option value="1">开启（默认）</option>
          <option value="0">关闭</option>
        </Select>
      </Field>
    </div>

    <div class="mt-4">
      <Field
        label="模型重定向 model_mapping（JSON，可选）"
        help='把请求里的模型名映射到上游真实模型，如 {"gpt-4":"gpt-4o","claude-3":"claude-3-5-sonnet"}。留空表示不重定向。'
      >
        <Textarea
          :rows="4"
          :model-value="form.model_mapping"
          @update:model-value="patch('model_mapping', $event)"
          placeholder='{"gpt-4": "gpt-4o"}'
          spellcheck="false"
        />
      </Field>
    </div>

    <p v-if="groupNames.length" class="mt-3 text-[11px] text-ink-soft">
      当前主站已知分组：{{ groupNames.join("、") }}
    </p>

    <div
      v-if="error"
      class="mt-3 rounded-[var(--radius-md)] bg-danger-bg px-3 py-2 text-[12.5px] text-danger-fg"
    >
      {{ error }}
    </div>

    <div class="mt-5 flex justify-end gap-2">
      <Button variant="secondary" :disabled="saving" @click="emit('close')">
        取消
      </Button>
      <Button :disabled="saving" @click="handleSubmit">
        {{ saving ? "保存中..." : editing ? "保存修改" : "创建渠道" }}
      </Button>
    </div>
  </Modal>
</template>
