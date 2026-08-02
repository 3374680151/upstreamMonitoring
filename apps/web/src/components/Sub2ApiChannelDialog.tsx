import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
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
import { Sub2ApiPricingEditor } from "./Sub2ApiPricingEditor";
import {
  Button,
  Field,
  Input,
  Modal,
  Select,
  SwitchRow,
  Tabs,
  Textarea,
} from "./ui";

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

export function Sub2ApiChannelDialog({
  open,
  channel,
  groups,
  onClose,
  onSubmit,
}: {
  open: boolean;
  channel: Channel | null;
  groups: Sub2ApiGroupRef[];
  onClose: () => void;
  onSubmit: (patch: Partial<Channel>) => Promise<void>;
}) {
  const [activeTab, setActiveTab] = useState<EditorTab>("basic");
  const [original, setOriginal] = useState<Channel | null>(null);
  const [draft, setDraft] = useState<Channel | null>(null);
  const [mapping, setMapping] = useState<MappingRow[]>([]);
  const [featuresConfigText, setFeaturesConfigText] = useState("{}");
  const [featuresConfigError, setFeaturesConfigError] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open || !channel) return;
    const next = cloneChannel(channel);
    setOriginal(next);
    setDraft(cloneChannel(channel));
    setMapping(mappingRows(channel.model_mapping));
    setFeaturesConfigText(
      JSON.stringify(channel.features_config || {}, null, 2),
    );
    setFeaturesConfigError("");
    setError("");
    setActiveTab("basic");
  }, [open, channel?.id]);

  function parseFeaturesConfig(): Record<string, unknown> | null {
    try {
      const parsed = featuresConfigText.trim()
        ? (JSON.parse(featuresConfigText) as unknown)
        : {};
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setFeaturesConfigError("features_config 必须是 JSON 对象");
        return null;
      }
      setFeaturesConfigError("");
      return parsed as Record<string, unknown>;
    } catch {
      setFeaturesConfigError("features_config 不是有效的 JSON");
      return null;
    }
  }

  async function save() {
    if (!draft || !original) return;
    if (!String(draft.name || "").trim()) {
      setError("渠道名称不能为空");
      setActiveTab("basic");
      return;
    }
    const featuresConfig = parseFeaturesConfig();
    if (!featuresConfig) {
      setActiveTab("advanced");
      return;
    }
    const candidate: Channel = {
      ...draft,
      name: String(draft.name || "").trim(),
      model_mapping: mappingRecord(mapping),
      features_config: featuresConfig,
    };
    const patch = buildSub2ApiChannelPatch(original, candidate);
    if (!Object.keys(patch).length) {
      setError("没有需要保存的修改");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onSubmit(patch);
      onClose();
    } catch (saveError) {
      setError(
        saveError instanceof Error ? saveError.message : String(saveError),
      );
    } finally {
      setSaving(false);
    }
  }

  const rules = draft?.account_stats_pricing_rules || [];

  return (
    <Modal
      open={open}
      title={`编辑 sub2api 渠道 · ${channel?.name || channel?.id || "渠道"}`}
      subtitle="渠道配置实时读取，保存后立即写回主站"
      onClose={saving ? () => {} : onClose}
      wide
    >
      <div className="flex flex-col gap-4">
        <Tabs
          items={tabs}
          value={activeTab}
          onChange={setActiveTab}
          label="sub2api 渠道配置"
        />

        {draft ? (
          <div
            id={`tab-panel-${activeTab}`}
            role="tabpanel"
            className="min-h-[22rem]"
          >
            {activeTab === "basic" ? (
              <div className="grid gap-4 md:grid-cols-2">
                <Field label="渠道名称">
                  <Input
                    value={String(draft.name || "")}
                    onChange={(event) =>
                      setDraft((previous) =>
                        previous
                          ? { ...previous, name: event.target.value }
                          : previous,
                      )
                    }
                  />
                </Field>
                <div className="md:pt-[1.375rem]">
                  <SwitchRow
                    label={draft.status === "active" ? "渠道启用" : "渠道停用"}
                    checked={draft.status === "active"}
                    onChange={(checked) =>
                      setDraft((previous) =>
                        previous
                          ? {
                              ...previous,
                              status: checked ? "active" : "disabled",
                            }
                          : previous,
                      )
                    }
                  />
                </div>
                <div className="md:col-span-2">
                  <Field label="渠道描述">
                    <Textarea
                      rows={5}
                      className="font-sans text-sm"
                      value={String(draft.description || "")}
                      onChange={(event) =>
                        setDraft((previous) =>
                          previous
                            ? { ...previous, description: event.target.value }
                            : previous,
                        )
                      }
                    />
                  </Field>
                </div>
              </div>
            ) : null}

            {activeTab === "groups" ? (
              <div className="overflow-hidden border border-line">
                {groups.length ? (
                  <div className="divide-y divide-line-soft">
                    {groups.map((group) => {
                      const selected = (draft.group_ids || []).includes(group.id);
                      return (
                        <label
                          key={group.id}
                          className="flex cursor-pointer items-center gap-3 px-3 py-3 hover:bg-sunken-hover"
                        >
                          <input
                            type="checkbox"
                            checked={selected}
                            onChange={(event) => {
                              const next = event.target.checked
                                ? [...(draft.group_ids || []), group.id]
                                : (draft.group_ids || []).filter(
                                    (id) => id !== group.id,
                                  );
                              setDraft({ ...draft, group_ids: next });
                            }}
                            className="h-4 w-4 accent-[var(--color-accent)]"
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block text-sm font-semibold text-ink-strong">
                              {group.name}
                            </span>
                            <span className="block text-[12.5px] text-ink-soft">
                              {group.platform || "未标注平台"} · #{group.id}
                            </span>
                          </span>
                          <span className="rounded-full bg-success-bg px-2 py-0.5 text-[12.5px] font-semibold tabular-nums text-success-fg">
                            {formatMultiplier(group.rate_multiplier)}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                ) : (
                  <div className="px-4 py-10 text-center text-sm text-ink-muted">
                    主站未返回可绑定分组
                  </div>
                )}
              </div>
            ) : null}

            {activeTab === "pricing" ? (
              <Sub2ApiPricingEditor
                value={draft.model_pricing || []}
                onChange={(model_pricing) =>
                  setDraft({ ...draft, model_pricing })
                }
              />
            ) : null}

            {activeTab === "mapping" ? (
              <div className="space-y-3">
                {mapping.length ? (
                  <div className="divide-y divide-line-soft border border-line">
                    {mapping.map((row, index) => (
                      <div
                        key={`mapping-${index}`}
                        className="grid gap-2 p-3 md:grid-cols-[1fr_1.3fr_1.3fr_auto]"
                      >
                        <Field label="平台">
                          <Input
                            value={row.platform}
                            onChange={(event) =>
                              setMapping((previous) =>
                                previous.map((item, itemIndex) =>
                                  itemIndex === index
                                    ? { ...item, platform: event.target.value }
                                    : item,
                                ),
                              )
                            }
                          />
                        </Field>
                        <Field label="源模型">
                          <Input
                            value={row.source}
                            onChange={(event) =>
                              setMapping((previous) =>
                                previous.map((item, itemIndex) =>
                                  itemIndex === index
                                    ? { ...item, source: event.target.value }
                                    : item,
                                ),
                              )
                            }
                          />
                        </Field>
                        <Field label="目标模型">
                          <Input
                            value={row.target}
                            onChange={(event) =>
                              setMapping((previous) =>
                                previous.map((item, itemIndex) =>
                                  itemIndex === index
                                    ? { ...item, target: event.target.value }
                                    : item,
                                ),
                              )
                            }
                          />
                        </Field>
                        <div className="flex items-end pb-0.5">
                          <button
                            type="button"
                            className={iconButtonClass}
                            title="移除模型映射"
                            aria-label="移除模型映射"
                            onClick={() =>
                              setMapping((previous) =>
                                previous.filter((_, itemIndex) => itemIndex !== index),
                              )
                            }
                          >
                            <Trash2 size={15} />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="border border-dashed border-line px-4 py-10 text-center text-sm text-ink-muted">
                    暂无模型映射
                  </div>
                )}
                <Button
                  variant="secondary"
                  onClick={() =>
                    setMapping((previous) => [
                      ...previous,
                      { platform: "", source: "", target: "" },
                    ])
                  }
                >
                  <Plus size={15} />
                  添加映射
                </Button>
              </div>
            ) : null}

            {activeTab === "advanced" ? (
              <div className="flex flex-col gap-6 md:gap-8">
                <div className="grid gap-3 md:grid-cols-2">
                  <Field label="计费模型来源 billing_model_source">
                    <Select
                      value={draft.billing_model_source || "channel_mapped"}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          billing_model_source: event.target.value,
                        })
                      }
                    >
                      <option value="channel_mapped">渠道映射模型</option>
                      <option value="requested">请求模型</option>
                      <option value="upstream">上游最终模型</option>
                    </Select>
                  </Field>
                  <Field label="特性 features" help="逗号或换行分隔">
                    <Input
                      value={sub2ApiFeaturesText(draft.features)}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          features: editedSub2ApiFeatures(
                            draft.features,
                            event.target.value,
                          ),
                        })
                      }
                    />
                  </Field>
                  <SwitchRow
                    label="限制未配置模型"
                    checked={Boolean(draft.restrict_models)}
                    onChange={(restrict_models) =>
                      setDraft({ ...draft, restrict_models })
                    }
                  />
                  <SwitchRow
                    label="将渠道定价用于账户统计"
                    checked={Boolean(draft.apply_pricing_to_account_stats)}
                    onChange={(apply_pricing_to_account_stats) =>
                      setDraft({ ...draft, apply_pricing_to_account_stats })
                    }
                  />
                </div>

                <Field label="features_config (JSON)">
                  <Textarea
                    rows={7}
                    value={featuresConfigText}
                    onChange={(event) => setFeaturesConfigText(event.target.value)}
                    onBlur={parseFeaturesConfig}
                  />
                </Field>
                {featuresConfigError ? (
                  <div className="text-[12.5px] text-danger-fg">
                    {featuresConfigError}
                  </div>
                ) : null}

                <div className="border-t border-line-soft pt-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div className="text-sm font-semibold text-ink-strong">
                      账户统计计费规则
                    </div>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() =>
                        setDraft({
                          ...draft,
                          account_stats_pricing_rules: [
                            ...rules,
                            emptyAccountRule(),
                          ],
                        })
                      }
                    >
                      <Plus size={14} />
                      添加规则
                    </Button>
                  </div>

                  {rules.length ? (
                    <div className="flex flex-col gap-4">
                      {rules.map((rule, ruleIndex) => (
                        <section
                          key={rule.id ?? `rule-${ruleIndex}`}
                          className="border border-line"
                        >
                          <div className="flex items-center justify-between border-b border-line-soft bg-panel-soft px-3 py-2">
                            <span className="text-[12.5px] font-semibold text-ink-strong">
                              统计规则 {ruleIndex + 1}
                            </span>
                            <button
                              type="button"
                              className={iconButtonClass}
                              title="移除统计规则"
                              aria-label="移除统计规则"
                              onClick={() =>
                                setDraft({
                                  ...draft,
                                  account_stats_pricing_rules: rules.filter(
                                    (_, index) => index !== ruleIndex,
                                  ),
                                })
                              }
                            >
                              <Trash2 size={15} />
                            </button>
                          </div>
                          <div className="grid gap-3 p-3 md:grid-cols-3">
                            <Field label="规则名称">
                              <Input
                                value={rule.name || ""}
                                onChange={(event) =>
                                  setDraft({
                                    ...draft,
                                    account_stats_pricing_rules: rules.map(
                                      (item, index) =>
                                        index === ruleIndex
                                          ? { ...item, name: event.target.value }
                                          : item,
                                    ),
                                  })
                                }
                              />
                            </Field>
                            <Field label="分组 ID" help="逗号分隔">
                              <Input
                                value={(rule.group_ids || []).join(",")}
                                onChange={(event) =>
                                  setDraft({
                                    ...draft,
                                    account_stats_pricing_rules: rules.map(
                                      (item, index) =>
                                        index === ruleIndex
                                          ? {
                                              ...item,
                                              group_ids: numberList(event.target.value),
                                            }
                                          : item,
                                    ),
                                  })
                                }
                              />
                            </Field>
                            <Field label="账户 ID" help="仅编辑配置中的 ID，不读取号池">
                              <Input
                                value={(rule.account_ids || []).join(",")}
                                onChange={(event) =>
                                  setDraft({
                                    ...draft,
                                    account_stats_pricing_rules: rules.map(
                                      (item, index) =>
                                        index === ruleIndex
                                          ? {
                                              ...item,
                                              account_ids: numberList(event.target.value),
                                            }
                                          : item,
                                    ),
                                  })
                                }
                              />
                            </Field>
                          </div>
                          <div className="border-t border-line-soft p-3">
                            <Sub2ApiPricingEditor
                              value={rule.pricing || []}
                              onChange={(pricing) =>
                                setDraft({
                                  ...draft,
                                  account_stats_pricing_rules: rules.map(
                                    (item, index) =>
                                      index === ruleIndex
                                        ? { ...item, pricing }
                                        : item,
                                  ),
                                })
                              }
                            />
                          </div>
                        </section>
                      ))}
                    </div>
                  ) : (
                    <div className="border border-dashed border-line px-4 py-8 text-center text-sm text-ink-muted">
                      暂无账户统计计费规则
                    </div>
                  )}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        {error ? (
          <div className="rounded-[var(--radius-md)] bg-danger-bg px-3 py-2 text-[12.5px] text-danger-fg">
            {error}
          </div>
        ) : null}
        <div className="flex justify-end gap-2 border-t border-line-soft pt-4">
          <Button variant="secondary" onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button onClick={save} loading={saving}>
            保存配置
          </Button>
        </div>
      </div>
    </Modal>
  );
}
