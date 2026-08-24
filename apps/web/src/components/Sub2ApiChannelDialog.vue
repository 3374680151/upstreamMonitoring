<script setup lang="ts">
import { computed, ref, shallowRef, watch } from "vue";
import { Plus, Trash2 } from "lucide-vue-next";
import {
  buildSub2ApiChannelPatch,
  editedSub2ApiFeatures,
  sub2ApiFeaturesText,
} from "@/lib/sub2apiChannel";
import type {
  Channel,
  Sub2ApiAccountStatsPricingRule,
  Sub2ApiGroupRef,
} from "@/lib/types";
import Sub2ApiPricingEditor from "./Sub2ApiPricingEditor.vue";
import {
  Button,
  Field,
  Input,
  Modal,
  Select,
  SwitchRow,
  Tabs,
  Textarea,
} from "@/components/ui";

type EditorTab = "basic" | "groups" | "pricing" | "mapping" | "advanced";
type MappingRow = { platform: string; source: string; target: string };

const tabs: Array<{ id: EditorTab; label: string }> = [
  { id: "basic", label: "基本信息" },
  { id: "groups", label: "绑定分组" },
  { id: "pricing", label: "模型定价" },
  { id: "mapping", label: "模型映射" },
  { id: "advanced", label: "高级计费" },
];

const iconButtonClass =
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-sm)] border border-line bg-panel text-ink-muted transition hover:bg-sunken-hover hover:text-danger-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]";

function cloneChannel(channel: Channel): Channel {
  return JSON.parse(JSON.stringify(channel)) as Channel;
}

function mappingRows(value: Channel["model_mapping"]): MappingRow[] {
  if (!value || typeof value !== "object") return [];
  const rows: MappingRow[] = [];
  for (const [platform, mappings] of Object.entries(value)) {
    if (!mappings || typeof mappings !== "object") continue;
    for (const [source, target] of Object.entries(mappings)) {
      rows.push({ platform, source, target: String(target || "") });
    }
  }
  return rows;
}

function mappingRecord(rows: MappingRow[]): Record<string, Record<string, string>> {
  const result: Record<string, Record<string, string>> = {};
  for (const row of rows) {
    const platform = row.platform.trim();
    const source = row.source.trim();
    const target = row.target.trim();
    if (!platform || !source || !target) continue;
    result[platform] = { ...(result[platform] || {}), [source]: target };
  }
  return result;
}

function numberList(value: string): number[] {
  return [
    ...new Set(
      value
        .split(/[\s,]+/)
        .map((item) => Number(item.trim()))
        .filter((item) => Number.isInteger(item) && item > 0),
    ),
  ];
}

function emptyAccountRule(): Sub2ApiAccountStatsPricingRule {
  return { name: "", group_ids: [], account_ids: [], pricing: [] };
}

function formatMultiplier(value?: number | null): string {
  return value === undefined || value === null ? "--" : `x${value}`;
}

interface Props {
  open: boolean;
  channel: Channel | null;
  groups: Sub2ApiGroupRef[];
  onSubmit: (patch: Partial<Channel>) => Promise<void>;
}
const props = defineProps<Props>();
const emit = defineEmits<{ close: [] }>();

const activeTab = ref<EditorTab>("basic");
const original = shallowRef<Channel | null>(null);
const draft = shallowRef<Channel | null>(null);
const mapping = ref<MappingRow[]>([]);
const featuresConfigText = ref("{}");
const featuresConfigError = ref("");
const saving = ref(false);
const error = ref("");

const rules = computed(
  () => draft.value?.account_stats_pricing_rules || [],
);

watch(
  () => [props.open, props.channel?.id] as [boolean, number | undefined],
  () => {
    if (!props.open || !props.channel) return;
    const ch = props.channel;
    original.value = cloneChannel(ch);
    draft.value = cloneChannel(ch);
    mapping.value = mappingRows(ch.model_mapping);
    featuresConfigText.value = JSON.stringify(
      ch.features_config || {},
      null,
      2,
    );
    featuresConfigError.value = "";
    error.value = "";
    activeTab.value = "basic";
  },
  { immediate: true },
);

function updateDraft(patch: Partial<Channel>) {
  if (draft.value) {
    draft.value = { ...draft.value, ...patch };
  }
}

function parseFeaturesConfig(): Record<string, unknown> | null {
  try {
    const parsed = featuresConfigText.value.trim()
      ? (JSON.parse(featuresConfigText.value) as unknown)
      : {};
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      featuresConfigError.value = "features_config 必须是 JSON 对象";
      return null;
    }
    featuresConfigError.value = "";
    return parsed as Record<string, unknown>;
  } catch {
    featuresConfigError.value = "features_config 不是有效的 JSON";
    return null;
  }
}

async function save() {
  if (!draft.value || !original.value) return;
  if (!String(draft.value.name || "").trim()) {
    error.value = "渠道名称不能为空";
    activeTab.value = "basic";
    return;
  }
  const featuresConfig = parseFeaturesConfig();
  if (!featuresConfig) {
    activeTab.value = "advanced";
    return;
  }
  const candidate: Channel = {
    ...draft.value,
    name: String(draft.value.name || "").trim(),
    model_mapping: mappingRecord(mapping.value),
    features_config: featuresConfig,
  };
  const patch = buildSub2ApiChannelPatch(original.value, candidate);
  if (!Object.keys(patch).length) {
    error.value = "没有需要保存的修改";
    return;
  }
  saving.value = true;
  error.value = "";
  try {
    await props.onSubmit(patch);
    emit("close");
  } catch (saveError) {
    error.value = saveError instanceof Error ? saveError.message : String(saveError);
  } finally {
    saving.value = false;
  }
}

function handleModalClose() {
  if (saving.value) return;
  emit("close");
}

function handleClose() {
  emit("close");
}

function toggleGroup(groupId: number, event: Event) {
  if (!draft.value) return;
  const checked = (event.target as HTMLInputElement).checked;
  const current = draft.value.group_ids || [];
  const next = checked
    ? [...current, groupId]
    : current.filter((id) => id !== groupId);
  updateDraft({ group_ids: next });
}

function updateMappingRow(index: number, patch: Partial<MappingRow>) {
  mapping.value = mapping.value.map((item, itemIndex) =>
    itemIndex === index ? { ...item, ...patch } : item,
  );
}

function addMappingRow() {
  mapping.value = [...mapping.value, { platform: "", source: "", target: "" }];
}

function removeMappingRow(index: number) {
  mapping.value = mapping.value.filter((_, itemIndex) => itemIndex !== index);
}

function updateRule(
  ruleIndex: number,
  patch: Partial<Sub2ApiAccountStatsPricingRule>,
) {
  if (!draft.value) return;
  const currentRules = draft.value.account_stats_pricing_rules || [];
  updateDraft({
    account_stats_pricing_rules: currentRules.map((item, index) =>
      index === ruleIndex ? { ...item, ...patch } : item,
    ),
  });
}

function addRule() {
  if (!draft.value) return;
  const currentRules = draft.value.account_stats_pricing_rules || [];
  updateDraft({
    account_stats_pricing_rules: [...currentRules, emptyAccountRule()],
  });
}

function removeRule(ruleIndex: number) {
  if (!draft.value) return;
  const currentRules = draft.value.account_stats_pricing_rules || [];
  updateDraft({
    account_stats_pricing_rules: currentRules.filter(
      (_, index) => index !== ruleIndex,
    ),
  });
}
</script>

<template>
  <Modal
    :open="open"
    :title="`编辑 sub2api 渠道 · ${channel?.name || channel?.id || '渠道'}`"
    subtitle="渠道配置实时读取，保存后立即写回主站"
    wide
    @close="handleModalClose"
  >
    <div class="flex flex-col gap-4">
      <Tabs
        v-model="activeTab"
        :items="tabs"
        label="sub2api 渠道配置"
      />

      <div v-if="draft" :id="`tab-panel-${activeTab}`" role="tabpanel" class="min-h-[22rem]">
        <template v-if="activeTab === 'basic'">
          <div class="grid gap-4 md:grid-cols-2">
            <Field label="渠道名称">
              <Input
                :model-value="draft.name || ''"
                @update:model-value="(val: string) => updateDraft({ name: val })"
              />
            </Field>
            <div class="md:pt-[1.375rem]">
              <SwitchRow
                :label="draft.status === 'active' ? '渠道启用' : '渠道停用'"
                :checked="draft.status === 'active'"
                @update:checked="(checked: boolean) => updateDraft({ status: checked ? 'active' : 'disabled' })"
              />
            </div>
            <div class="md:col-span-2">
              <Field label="渠道描述">
                <Textarea
                  rows="5"
                  class="font-sans text-sm"
                  :model-value="draft.description || ''"
                  @update:model-value="(val: string) => updateDraft({ description: val })"
                />
              </Field>
            </div>
          </div>
        </template>

        <template v-else-if="activeTab === 'groups'">
          <div class="overflow-hidden border border-line">
            <template v-if="groups.length">
              <div class="divide-y divide-line-soft">
                <label
                  v-for="group in groups"
                  :key="group.id"
                  class="flex cursor-pointer items-center gap-3 px-3 py-3 hover:bg-sunken-hover"
                >
                  <input
                    type="checkbox"
                    :checked="(draft.group_ids || []).includes(group.id)"
                    @change="toggleGroup(group.id, $event)"
                    class="h-4 w-4 accent-[var(--color-accent)]"
                  />
                  <span class="min-w-0 flex-1">
                    <span class="block text-sm font-semibold text-ink-strong">{{ group.name }}</span>
                    <span class="block text-[12.5px] text-ink-soft">{{ group.platform || '未标注平台' }} · #{{ group.id }}</span>
                  </span>
                  <span class="rounded-full bg-success-bg px-2 py-0.5 text-[12.5px] font-semibold tabular-nums text-success-fg">
                    {{ formatMultiplier(group.rate_multiplier) }}
                  </span>
                </label>
              </div>
            </template>
            <template v-else>
              <div class="px-4 py-10 text-center text-sm text-ink-muted">
                主站未返回可绑定分组
              </div>
            </template>
          </div>
        </template>

        <template v-else-if="activeTab === 'pricing'">
          <Sub2ApiPricingEditor
            :value="draft.model_pricing || []"
            @change="(model_pricing) => updateDraft({ model_pricing })"
          />
        </template>

        <template v-else-if="activeTab === 'mapping'">
          <div class="space-y-3">
            <template v-if="mapping.length">
              <div class="divide-y divide-line-soft border border-line">
                <div
                  v-for="(row, index) in mapping"
                  :key="`mapping-${index}`"
                  class="grid gap-2 p-3 md:grid-cols-[1fr_1.3fr_1.3fr_auto]"
                >
                  <Field label="平台">
                    <Input
                      :model-value="row.platform"
                      @update:model-value="(val: string) => updateMappingRow(index, { platform: val })"
                    />
                  </Field>
                  <Field label="源模型">
                    <Input
                      :model-value="row.source"
                      @update:model-value="(val: string) => updateMappingRow(index, { source: val })"
                    />
                  </Field>
                  <Field label="目标模型">
                    <Input
                      :model-value="row.target"
                      @update:model-value="(val: string) => updateMappingRow(index, { target: val })"
                    />
                  </Field>
                  <div class="flex items-end pb-0.5">
                    <button
                      type="button"
                      :class="iconButtonClass"
                      title="移除模型映射"
                      aria-label="移除模型映射"
                      @click="removeMappingRow(index)"
                    >
                      <Trash2 :size="15" />
                    </button>
                  </div>
                </div>
              </div>
            </template>
            <template v-else>
              <div class="border border-dashed border-line px-4 py-10 text-center text-sm text-ink-muted">
                暂无模型映射
              </div>
            </template>
            <Button variant="secondary" @click="addMappingRow">
              <Plus :size="15" />
              添加映射
            </Button>
          </div>
        </template>

        <template v-else-if="activeTab === 'advanced'">
          <div class="flex flex-col gap-6 md:gap-8">
            <div class="grid gap-3 md:grid-cols-2">
              <Field label="计费模型来源 billing_model_source">
                <Select
                  :model-value="draft.billing_model_source || 'channel_mapped'"
                  @update:model-value="(val: string) => updateDraft({ billing_model_source: val })"
                >
                  <option value="channel_mapped">渠道映射模型</option>
                  <option value="requested">请求模型</option>
                  <option value="upstream">上游最终模型</option>
                </Select>
              </Field>
              <Field label="特性 features" help="逗号或换行分隔">
                <Input
                  :model-value="sub2ApiFeaturesText(draft.features)"
                  @update:model-value="(val: string) => updateDraft({ features: editedSub2ApiFeatures(draft?.features, val) })"
                />
              </Field>
              <SwitchRow
                label="限制未配置模型"
                :checked="Boolean(draft.restrict_models)"
                @update:checked="(restrict_models: boolean) => updateDraft({ restrict_models })"
              />
              <SwitchRow
                label="将渠道定价用于账户统计"
                :checked="Boolean(draft.apply_pricing_to_account_stats)"
                @update:checked="(apply_pricing_to_account_stats: boolean) => updateDraft({ apply_pricing_to_account_stats })"
              />
            </div>

            <Field label="features_config (JSON)">
              <Textarea
                rows="7"
                v-model="featuresConfigText"
                @blur="parseFeaturesConfig"
              />
            </Field>
            <div v-if="featuresConfigError" class="text-[12.5px] text-danger-fg">
              {{ featuresConfigError }}
            </div>

            <div class="border-t border-line-soft pt-4">
              <div class="mb-3 flex items-center justify-between gap-3">
                <div class="text-sm font-semibold text-ink-strong">
                  账户统计计费规则
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  @click="addRule"
                >
                  <Plus :size="14" />
                  添加规则
                </Button>
              </div>

              <template v-if="rules.length">
                <div class="flex flex-col gap-4">
                  <section
                    v-for="(rule, ruleIndex) in rules"
                    :key="rule.id ?? `rule-${ruleIndex}`"
                    class="border border-line"
                  >
                    <div class="flex items-center justify-between border-b border-line-soft bg-panel-soft px-3 py-2">
                      <span class="text-[12.5px] font-semibold text-ink-strong">
                        统计规则 {{ ruleIndex + 1 }}
                      </span>
                      <button
                        type="button"
                        :class="iconButtonClass"
                        title="移除统计规则"
                        aria-label="移除统计规则"
                        @click="removeRule(ruleIndex)"
                      >
                        <Trash2 :size="15" />
                      </button>
                    </div>
                    <div class="grid gap-3 p-3 md:grid-cols-3">
                      <Field label="规则名称">
                        <Input
                          :model-value="rule.name || ''"
                          @update:model-value="(val: string) => updateRule(ruleIndex, { name: val })"
                        />
                      </Field>
                      <Field label="分组 ID" help="逗号分隔">
                        <Input
                          :model-value="(rule.group_ids || []).join(',')"
                          @update:model-value="(val: string) => updateRule(ruleIndex, { group_ids: numberList(val) })"
                        />
                      </Field>
                      <Field label="账户 ID" help="仅编辑配置中的 ID，不读取号池">
                        <Input
                          :model-value="(rule.account_ids || []).join(',')"
                          @update:model-value="(val: string) => updateRule(ruleIndex, { account_ids: numberList(val) })"
                        />
                      </Field>
                    </div>
                    <div class="border-t border-line-soft p-3">
                      <Sub2ApiPricingEditor
                        :value="rule.pricing || []"
                        @change="(pricing) => updateRule(ruleIndex, { pricing })"
                      />
                    </div>
                  </section>
                </div>
              </template>
              <template v-else>
                <div class="border border-dashed border-line px-4 py-8 text-center text-sm text-ink-muted">
                  暂无账户统计计费规则
                </div>
              </template>
            </div>
          </div>
        </template>
      </div>

      <div v-if="error" class="rounded-[var(--radius-md)] bg-danger-bg px-3 py-2 text-[12.5px] text-danger-fg">
        {{ error }}
      </div>
      <div class="flex justify-end gap-2 border-t border-line-soft pt-4">
        <Button variant="secondary" :disabled="saving" @click="handleClose">
          取消
        </Button>
        <Button :loading="saving" @click="save">
          保存配置
        </Button>
      </div>
    </div>
  </Modal>
</template>
