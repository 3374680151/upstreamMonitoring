import { Plus, Trash2 } from "lucide-react";
import type {
  Sub2ApiModelPricing,
  Sub2ApiPricingInterval,
} from "@/lib/types";
import {
  sub2ApiMTokToPerToken,
  sub2ApiPerTokenToMTok,
} from "@/lib/sub2apiChannel";
import { Button, Field, Input, Select, Textarea } from "./ui";

const iconButtonClass =
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-sm)] border border-line bg-panel text-ink-muted transition hover:bg-sunken-hover hover:text-danger-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]";

function emptyPricing(): Sub2ApiModelPricing {
  return {
    platform: "",
    models: [],
    billing_mode: "token",
    input_price: null,
    output_price: null,
    cache_write_price: null,
    cache_read_price: null,
    image_input_price: null,
    image_output_price: null,
    per_request_price: null,
    intervals: [],
  };
}

function emptyInterval(): Sub2ApiPricingInterval {
  return {
    min_tokens: 0,
    max_tokens: null,
    tier_label: "",
    input_price: null,
    output_price: null,
    cache_write_price: null,
    cache_read_price: null,
    per_request_price: null,
    sort_order: 0,
  };
}

function nullableNumber(value: string): number | null {
  if (value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function uniqueList(value: string): string[] {
  return [...new Set(value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean))];
}

function NumberField({
  label,
  value,
  onChange,
  integer = false,
  perMTok = false,
}: {
  label: string;
  value?: number | null;
  onChange: (value: number | null) => void;
  integer?: boolean;
  perMTok?: boolean;
}) {
  const displayValue = perMTok ? sub2ApiPerTokenToMTok(value) : value;
  return (
    <Field label={label}>
      <Input
        type="number"
        step={integer ? 1 : "any"}
        min={perMTok ? 0 : undefined}
        value={displayValue ?? ""}
        onChange={(event) => {
          const nextValue = nullableNumber(event.target.value);
          onChange(perMTok ? sub2ApiMTokToPerToken(nextValue) : nextValue);
        }}
        className="tabular-nums"
      />
    </Field>
  );
}

export function Sub2ApiPricingEditor({
  value,
  onChange,
}: {
  value: Sub2ApiModelPricing[];
  onChange: (value: Sub2ApiModelPricing[]) => void;
}) {
  function changeRow(
    index: number,
    updater: (row: Sub2ApiModelPricing) => Sub2ApiModelPricing,
  ) {
    onChange(value.map((row, rowIndex) => (rowIndex === index ? updater(row) : row)));
  }

  function changeInterval(
    rowIndex: number,
    intervalIndex: number,
    updater: (interval: Sub2ApiPricingInterval) => Sub2ApiPricingInterval,
  ) {
    changeRow(rowIndex, (row) => ({
      ...row,
      intervals: (row.intervals || []).map((interval, index) =>
        index === intervalIndex ? updater(interval) : interval,
      ),
    }));
  }

  return (
    <div className="space-y-3">
      {value.length ? (
        value.map((pricing, rowIndex) => (
          <section
            key={pricing.id ?? `pricing-${rowIndex}`}
            className="border border-line bg-panel-soft"
          >
            <div className="flex items-center justify-between gap-3 border-b border-line-soft px-3 py-2">
              <div className="text-[12.5px] font-semibold text-ink-strong">
                定价规则 {rowIndex + 1}
              </div>
              <button
                type="button"
                className={iconButtonClass}
                title="移除定价规则"
                aria-label="移除定价规则"
                onClick={() => onChange(value.filter((_, index) => index !== rowIndex))}
              >
                <Trash2 size={15} />
              </button>
            </div>

            <div className="grid gap-3 p-3 md:grid-cols-2 xl:grid-cols-4">
              <Field label="平台 platform">
                <Input
                  value={pricing.platform || ""}
                  onChange={(event) =>
                    changeRow(rowIndex, (row) => ({
                      ...row,
                      platform: event.target.value,
                    }))
                  }
                  placeholder="anthropic"
                />
              </Field>
              <Field label="计费模式">
                <Select
                  value={pricing.billing_mode || "token"}
                  onChange={(event) =>
                    changeRow(rowIndex, (row) => ({
                      ...row,
                      billing_mode: event.target.value,
                    }))
                  }
                >
                  <option value="token">Token</option>
                  <option value="per_request">按请求</option>
                  <option value="image">图片</option>
                </Select>
              </Field>
              <div className="md:col-span-2">
                <Field label="模型列表" help="逗号或换行分隔">
                  <Textarea
                    rows={2}
                    value={(pricing.models || []).join("\n")}
                    onChange={(event) =>
                      changeRow(rowIndex, (row) => ({
                        ...row,
                        models: uniqueList(event.target.value),
                      }))
                    }
                  />
                </Field>
              </div>

              <NumberField
                label="输入价 ($/MTok)"
                perMTok
                value={pricing.input_price}
                onChange={(input_price) =>
                  changeRow(rowIndex, (row) => ({ ...row, input_price }))
                }
              />
              <NumberField
                label="输出价 ($/MTok)"
                perMTok
                value={pricing.output_price}
                onChange={(output_price) =>
                  changeRow(rowIndex, (row) => ({ ...row, output_price }))
                }
              />
              <NumberField
                label="缓存写入价 ($/MTok)"
                perMTok
                value={pricing.cache_write_price}
                onChange={(cache_write_price) =>
                  changeRow(rowIndex, (row) => ({ ...row, cache_write_price }))
                }
              />
              <NumberField
                label="缓存读取价 ($/MTok)"
                perMTok
                value={pricing.cache_read_price}
                onChange={(cache_read_price) =>
                  changeRow(rowIndex, (row) => ({ ...row, cache_read_price }))
                }
              />
              <NumberField
                label="图片输入价 ($/MTok)"
                perMTok
                value={pricing.image_input_price}
                onChange={(image_input_price) =>
                  changeRow(rowIndex, (row) => ({ ...row, image_input_price }))
                }
              />
              <NumberField
                label="图片输出价 ($/MTok)"
                perMTok
                value={pricing.image_output_price}
                onChange={(image_output_price) =>
                  changeRow(rowIndex, (row) => ({ ...row, image_output_price }))
                }
              />
              <NumberField
                label="单请求价 ($)"
                value={pricing.per_request_price}
                onChange={(per_request_price) =>
                  changeRow(rowIndex, (row) => ({ ...row, per_request_price }))
                }
              />
            </div>

            <div className="border-t border-line-soft px-3 py-3">
              <div className="mb-2 flex items-center justify-between gap-3">
                <div className="text-[12.5px] font-semibold text-ink-muted">
                  Token 区间价
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    changeRow(rowIndex, (row) => ({
                      ...row,
                      intervals: [...(row.intervals || []), emptyInterval()],
                    }))
                  }
                >
                  <Plus size={14} />
                  添加区间
                </Button>
              </div>
              {(pricing.intervals || []).length ? (
                <div className="divide-y divide-line-soft">
                  {(pricing.intervals || []).map((interval, intervalIndex) => (
                    <div
                      key={interval.id ?? `interval-${intervalIndex}`}
                      className="grid gap-2 py-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6"
                    >
                      <NumberField
                        label="min_tokens"
                        integer
                        value={interval.min_tokens}
                        onChange={(min_tokens) =>
                          changeInterval(rowIndex, intervalIndex, (item) => ({
                            ...item,
                            min_tokens: min_tokens ?? 0,
                          }))
                        }
                      />
                      <NumberField
                        label="max_tokens"
                        integer
                        value={interval.max_tokens}
                        onChange={(max_tokens) =>
                          changeInterval(rowIndex, intervalIndex, (item) => ({
                            ...item,
                            max_tokens,
                          }))
                        }
                      />
                      <Field label="区间名称">
                        <Input
                          value={interval.tier_label || ""}
                          onChange={(event) =>
                            changeInterval(rowIndex, intervalIndex, (item) => ({
                              ...item,
                              tier_label: event.target.value,
                            }))
                          }
                        />
                      </Field>
                      <NumberField
                        label="输入价 ($/MTok)"
                        perMTok
                        value={interval.input_price}
                        onChange={(input_price) =>
                          changeInterval(rowIndex, intervalIndex, (item) => ({
                            ...item,
                            input_price,
                          }))
                        }
                      />
                      <NumberField
                        label="输出价 ($/MTok)"
                        perMTok
                        value={interval.output_price}
                        onChange={(output_price) =>
                          changeInterval(rowIndex, intervalIndex, (item) => ({
                            ...item,
                            output_price,
                          }))
                        }
                      />
                      <NumberField
                        label="缓存写入价 ($/MTok)"
                        perMTok
                        value={interval.cache_write_price}
                        onChange={(cache_write_price) =>
                          changeInterval(rowIndex, intervalIndex, (item) => ({
                            ...item,
                            cache_write_price,
                          }))
                        }
                      />
                      <NumberField
                        label="缓存读取价 ($/MTok)"
                        perMTok
                        value={interval.cache_read_price}
                        onChange={(cache_read_price) =>
                          changeInterval(rowIndex, intervalIndex, (item) => ({
                            ...item,
                            cache_read_price,
                          }))
                        }
                      />
                      <NumberField
                        label="单请求价 ($)"
                        value={interval.per_request_price}
                        onChange={(per_request_price) =>
                          changeInterval(rowIndex, intervalIndex, (item) => ({
                            ...item,
                            per_request_price,
                          }))
                        }
                      />
                      <NumberField
                        label="排序"
                        integer
                        value={interval.sort_order}
                        onChange={(sort_order) =>
                          changeInterval(rowIndex, intervalIndex, (item) => ({
                            ...item,
                            sort_order: sort_order ?? 0,
                          }))
                        }
                      />
                      <div className="flex items-end pb-0.5">
                        <button
                          type="button"
                          className={iconButtonClass}
                          title="移除价格区间"
                          aria-label="移除价格区间"
                          onClick={() =>
                            changeRow(rowIndex, (row) => ({
                              ...row,
                              intervals: (row.intervals || []).filter(
                                (_, index) => index !== intervalIndex,
                              ),
                            }))
                          }
                        >
                          <Trash2 size={15} />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="py-3 text-[12.5px] text-ink-soft">
                  未配置区间价
                </div>
              )}
            </div>
          </section>
        ))
      ) : (
        <div className="border border-dashed border-line px-4 py-8 text-center text-sm text-ink-muted">
          暂无模型定价
        </div>
      )}

      <Button
        variant="secondary"
        onClick={() => onChange([...value, emptyPricing()])}
      >
        <Plus size={15} />
        添加定价规则
      </Button>
    </div>
  );
}
