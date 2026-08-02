import { useEffect, useRef, useState } from "react";
import type {
  AuthMode,
  Channel,
  ChannelUpstreamBinding,
  ChannelUpstreamBindingPayload,
  Platform,
} from "@/lib/types";
import { Button, Field, Input, Modal, Select, Textarea } from "./ui";

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

export function ChannelFormDialog({
  open,
  channel,
  binding,
  groupNames,
  onClose,
  onSubmit,
}: {
  open: boolean;
  /** null = 新增；否则为编辑（key 字段留空表示不修改密钥） */
  channel: Channel | null;
  binding?: ChannelUpstreamBinding;
  groupNames: string[];
  onClose: () => void;
  onSubmit: (
    payload: Partial<Channel>,
    binding: ChannelUpstreamBindingPayload,
  ) => Promise<void>;
}) {
  const editing = !!channel;
  const [form, setForm] = useState<FormState>(toForm(channel, binding));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const formDirty = useRef(false);
  const formSessionId = useRef<number | null | undefined>(undefined);
  const bindingFormKey = binding
    ? [
        binding.upstream_base_url,
        binding.upstream_platform,
        binding.auth_mode,
        binding.access_user_id,
        binding.has_login_username,
        binding.has_login_password,
        binding.has_access_token,
        binding.has_refresh_token,
        binding.has_channel_key,
      ].join("|")
    : "";
  const originalKeyHint = channel?.key
    ? `当前密钥：${channel.key}（掩码）`
    : binding?.has_channel_key
      ? "当前密钥已保存，留空保持不变"
      : "尚未配置密钥";
  const bindingPlatform = (binding?.upstream_platform as Platform) || "newapi";
  const bindingAuthMode = (binding?.auth_mode as AuthMode) || "token";
  const sameBindingPlatform = bindingPlatform === form.upstream_platform;
  const sameBindingAuthMode = sameBindingPlatform && bindingAuthMode === form.upstream_auth_mode;
  const hasSavedNewApiToken = Boolean(
    sameBindingPlatform && form.upstream_platform === "newapi" && binding?.has_access_token,
  );
  const hasSavedSub2ApiPassword = Boolean(
    sameBindingAuthMode && form.upstream_auth_mode === "password" && binding?.has_login_password,
  );
  const hasSavedSub2ApiToken = Boolean(
    sameBindingAuthMode && form.upstream_auth_mode === "token" && binding?.has_access_token,
  );
  const hasSavedSub2ApiRefresh = Boolean(
    sameBindingAuthMode && form.upstream_auth_mode === "token" && binding?.has_refresh_token,
  );

  useEffect(() => {
    if (!open) {
      formDirty.current = false;
      formSessionId.current = undefined;
      return;
    }
    const recordId = channel?.id ?? null;
    if (formSessionId.current === recordId && formDirty.current) return;
    formSessionId.current = recordId;
    formDirty.current = false;
    setForm(toForm(channel, binding));
    setError("");
    // 绑定数据可能晚于弹窗打开到达；只有未输入时才用新数据补齐原值。
  }, [open, channel?.id, bindingFormKey]);

  function patch<K extends keyof FormState>(k: K, v: FormState[K]) {
    formDirty.current = true;
    setForm((prev) => ({ ...prev, [k]: v }));
  }

  async function handleSubmit() {
    if (!form.name.trim()) {
      setError("请填写渠道名称");
      return;
    }
    if (!editing && !form.key.trim()) {
      setError("新增渠道必须填写密钥");
      return;
    }
    // 模型重定向：允许留空；填写则必须是合法 JSON 对象（透传给官方 model_mapping）
    const mappingText = form.model_mapping.trim();
    if (mappingText) {
      try {
        const parsed = JSON.parse(mappingText);
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
          setError("模型重定向必须是 JSON 对象，如 {\"gpt-4\":\"gpt-4o\"}");
          return;
        }
      } catch {
        setError("模型重定向不是合法 JSON，请检查格式");
        return;
      }
    }
    const payload: Partial<Channel> = {
      name: form.name.trim(),
      type: form.type,
      base_url: form.base_url.trim(),
      models: form.models.trim(),
      group: form.group.trim() || "default",
      weight: Number(form.weight) || 0,
      priority: Number(form.priority) || 0,
      status: form.status,
      // 官方 model_mapping 存的是 JSON 字符串；留空表示清除重定向
      model_mapping: mappingText,
      tag: form.tag.trim(),
      test_model: form.test_model.trim(),
      auto_ban: form.auto_ban ? 1 : 0,
    };
    const upstreamPayload: ChannelUpstreamBindingPayload = {
      upstream_base_url: form.upstream_base_url.trim(),
      upstream_platform: form.upstream_platform,
      auth_mode: form.upstream_auth_mode,
      login_username: form.upstream_login_username.trim(),
      login_password: form.upstream_login_password,
      access_token: form.upstream_access_token.trim(),
      access_user_id: form.upstream_access_user_id.trim(),
      refresh_token: form.upstream_refresh_token.trim(),
      channel_key: form.key.trim(),
    };
    // 密钥：新增必填；编辑时留空 = 保持原密钥（后端 read-merge-write 不覆盖）
    if (form.key.trim()) payload.key = form.key.trim();
    setSaving(true);
    setError("");
    try {
      await onSubmit(payload, upstreamPayload);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      title={editing ? `编辑渠道 · ${channel?.name || channel?.id}` : "添加渠道"}
      subtitle="所有字段透传官方 /api/channel 接口，兼容官方后台配置"
      onClose={onClose}
      wide
    >
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Field label="渠道名称">
          <Input
            value={form.name}
            onChange={(e) => patch("name", e.target.value)}
            placeholder="例如 OpenAI 主号"
          />
        </Field>
        <Field label="渠道类型">
          <Select
            value={String(form.type)}
            onChange={(e) => patch("type", Number(e.target.value))}
          >
            {CHANNEL_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label} ({t.value})
              </option>
            ))}
            {CHANNEL_TYPES.every((t) => t.value !== form.type) ? (
              <option value={String(form.type)}>自定义 ({form.type})</option>
            ) : null}
          </Select>
        </Field>
        <Field
          label={editing ? "密钥（留空则不修改）" : "密钥"}
          help={
            form.upstream_platform === "sub2api"
              ? `sub2api 必须用这个实际 key 查询所属分组和倍率；${originalKeyHint}`
              : editing
                ? `${originalKeyHint}。为安全起见，编辑时不回显明文密钥`
                : "新增渠道必须填写密钥"
          }
        >
          <Input
            value={form.key}
            onChange={(e) => patch("key", e.target.value)}
            placeholder={editing ? "已保存，留空不修改" : "sk-..."}
            autoComplete="off"
          />
        </Field>
        <Field label="Base URL（可选）" help="留空使用官方该类型默认地址">
          <Input
            value={form.base_url}
            onChange={(e) => patch("base_url", e.target.value)}
            placeholder="https://api.openai.com"
          />
        </Field>
        <div className="space-y-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-panel-soft)] p-3 md:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="text-xs font-semibold text-[var(--color-text-muted)]">上游 key 匹配</div>
              <div className="mt-0.5 text-[11px] text-[var(--color-text-soft)]">
                优先使用本渠道单独配置的登录态；未配置时自动复用同 Base URL 的「渠道监控」登录态，按当前 key 精确匹配分组和倍率
              </div>
            </div>
            {binding?.configured ? (
              <span className="rounded-full bg-[var(--color-success-bg)] px-2.5 py-0.5 text-[10px] font-semibold text-[var(--color-success-text)]">
                已配置，密钥留空不修改
              </span>
            ) : null}
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <Field label="上游管理地址">
              <Input
                value={form.upstream_base_url}
                onChange={(e) => patch("upstream_base_url", e.target.value)}
                placeholder="https://upstream.example.com"
              />
            </Field>
            <Field label="上游平台">
              <Select
                value={form.upstream_platform}
                onChange={(e) => patch("upstream_platform", e.target.value as Platform)}
              >
                <option value="newapi">NewAPI</option>
                <option value="sub2api">sub2api</option>
              </Select>
            </Field>
            {form.upstream_platform === "newapi" ? (
              <>
                <Field
                  label="上游普通用户系统访问令牌"
                  help={hasSavedNewApiToken ? "当前已保存，留空保持原令牌" : "尚未配置，用于读取该用户自己的 API 密钥列表，不需要管理员权限"}
                >
                  <Input
                    type="password"
                    value={form.upstream_access_token}
                    onChange={(e) => patch("upstream_access_token", e.target.value)}
                    autoComplete="off"
                    placeholder={hasSavedNewApiToken ? "已保存，留空不修改" : "填写上游令牌"}
                  />
                </Field>
                <Field
                  label="上游用户 ID"
                  help={sameBindingPlatform && binding?.access_user_id ? "当前已配置，留空保持不变" : undefined}
                >
                  <Input
                    value={form.upstream_access_user_id}
                    onChange={(e) => patch("upstream_access_user_id", e.target.value)}
                    placeholder="例如：1"
                  />
                </Field>
              </>
            ) : (
              <>
                <Field label="上游认证方式">
                  <Select
                    value={form.upstream_auth_mode}
                    onChange={(e) => patch("upstream_auth_mode", e.target.value as AuthMode)}
                  >
                    <option value="password">账号密码</option>
                    <option value="token">导入登录态</option>
                  </Select>
                </Field>
                {form.upstream_auth_mode === "password" ? (
                  <>
                    <Field label="上游用户邮箱">
                      <Input
                        value={form.upstream_login_username}
                        onChange={(e) => patch("upstream_login_username", e.target.value)}
                      />
                    </Field>
                    <Field label="上游用户密码" help={hasSavedSub2ApiPassword ? "当前已保存，留空保持原密码" : "尚未配置，填写后用于登录"}>
                      <Input
                        type="password"
                        value={form.upstream_login_password}
                        onChange={(e) => patch("upstream_login_password", e.target.value)}
                        autoComplete="off"
                        placeholder={hasSavedSub2ApiPassword ? "已保存，留空不修改" : "填写上游密码"}
                      />
                    </Field>
                  </>
                ) : (
                  <>
                    <Field label="上游 auth_token" help={hasSavedSub2ApiToken ? "当前已保存，留空保持原 token" : "尚未配置，填写后导入登录态"}>
                      <Input
                        type="password"
                        value={form.upstream_access_token}
                        onChange={(e) => patch("upstream_access_token", e.target.value)}
                        autoComplete="off"
                        placeholder={hasSavedSub2ApiToken ? "已保存，留空不修改" : "填写 auth_token"}
                      />
                    </Field>
                    <Field
                      label="上游 refresh_token"
                      help={hasSavedSub2ApiRefresh ? "当前已保存，留空保持原 refresh_token" : "可选；auth_token 过期后用于自动刷新登录态"}
                    >
                      <Input
                        type="password"
                        value={form.upstream_refresh_token}
                        onChange={(e) => patch("upstream_refresh_token", e.target.value)}
                        autoComplete="off"
                        placeholder={hasSavedSub2ApiRefresh ? "已保存，留空不修改" : "可选"}
                      />
                    </Field>
                  </>
                )}
              </>
            )}
          </div>
        </div>
        <Field label="分组（逗号分隔）">
          <Input
            value={form.group}
            onChange={(e) => patch("group", e.target.value)}
            placeholder="default,vip"
          />
        </Field>
        <Field label="模型（逗号分隔，可选）">
          <Input
            value={form.models}
            onChange={(e) => patch("models", e.target.value)}
            placeholder="gpt-4o,gpt-4o-mini"
          />
        </Field>
        <Field label="权重 weight" help="同优先级内按权重做负载均衡">
          <Input
            type="number"
            min={0}
            value={form.weight}
            onChange={(e) => patch("weight", Number(e.target.value))}
          />
        </Field>
        <Field label="优先级 priority" help="数值越大越优先被调度">
          <Input
            type="number"
            value={form.priority}
            onChange={(e) => patch("priority", Number(e.target.value))}
          />
        </Field>
        <Field label="状态">
          <Select
            value={String(form.status)}
            onChange={(e) => patch("status", Number(e.target.value))}
          >
            <option value="1">启用</option>
            <option value="2">手动停用</option>
          </Select>
        </Field>
        <Field label="测速模型 test_model" help="留空则用渠道模型列表第一个">
          <Input
            value={form.test_model}
            onChange={(e) => patch("test_model", e.target.value)}
            placeholder="gpt-4o-mini"
          />
        </Field>
        <Field label="标签 tag" help="用于分组/批量管理，可选">
          <Input
            value={form.tag}
            onChange={(e) => patch("tag", e.target.value)}
            placeholder="例如 openai-主力"
          />
        </Field>
        <Field label="自动禁用 auto_ban" help="测试失败时是否自动停用该渠道">
          <Select
            value={String(form.auto_ban)}
            onChange={(e) => patch("auto_ban", Number(e.target.value))}
          >
            <option value="1">开启（默认）</option>
            <option value="0">关闭</option>
          </Select>
        </Field>
      </div>

      <div className="mt-4">
        <Field
          label="模型重定向 model_mapping（JSON，可选）"
          help='把请求里的模型名映射到上游真实模型，如 {"gpt-4":"gpt-4o","claude-3":"claude-3-5-sonnet"}。留空表示不重定向。'
        >
          <Textarea
            rows={4}
            value={form.model_mapping}
            onChange={(e) => patch("model_mapping", e.target.value)}
            placeholder='{"gpt-4": "gpt-4o"}'
            spellCheck={false}
          />
        </Field>
      </div>

      {groupNames.length ? (
        <p className="mt-3 text-[11px] text-[var(--color-text-soft)]">
          当前主站已知分组：{groupNames.join("、")}
        </p>
      ) : null}

      {error ? (
        <div className="mt-3 rounded-lg bg-[var(--color-danger-bg)] px-3 py-2 text-xs text-[var(--color-danger-text)]">
          {error}
        </div>
      ) : null}

      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose} disabled={saving}>
          取消
        </Button>
        <Button onClick={handleSubmit} disabled={saving}>
          {saving ? "保存中..." : editing ? "保存修改" : "创建渠道"}
        </Button>
      </div>
    </Modal>
  );
}
