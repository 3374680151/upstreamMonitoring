<script setup lang="ts">
import { Plus, Trash2 } from "lucide-vue-next";
import { Button, Field, Input, Select, Textarea } from "@/components/ui";
import Sub2ApiNumberField from "./Sub2ApiNumberField.vue";
import type {
  Sub2ApiModelPricing,
  Sub2ApiPricingInterval,
} from "@/lib/types";

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

function uniqueList(value: string): string[] {
  return [...new Set(value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean))];
}

interface Props {
  value: Sub2ApiModelPricing[];
}
const props = defineProps<Props>();
const emit = defineEmits<{ change: [value: Sub2ApiModelPricing[]] }>();

function changeRow(
  index: number,
  updater: (row: Sub2ApiModelPricing) => Sub2ApiModelPricing,
) {
  emit(
    "change",
    props.value.map((row, rowIndex) => (rowIndex === index ? updater(row) : row)),
  );
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
</script>

<template>
  <div class="space-y-3">
    <template v-if="value.length">
      <section
        v-for="(pricing, rowIndex) in value"
        :key="pricing.id ?? `pricing-${rowIndex}`"
        class="border border-line bg-panel-soft"
      >
        <div class="flex items-center justify-between gap-3 border-b border-line-soft px-3 py-2">
          <div class="text-[12.5px] font-semibold text-ink-strong">
            定价规则 {{ rowIndex + 1 }}
          </div>
          <button
            type="button"
            :class="iconButtonClass"
            title="移除定价规则"
            aria-label="移除定价规则"
            @click="emit('change', value.filter((_, index) => index !== rowIndex))"
          >
            <Trash2 :size="15" />
          </button>
        </div>

        <div class="grid gap-3 p-3 md:grid-cols-2 xl:grid-cols-4">
          <Field label="平台 platform">
            <Input
              :model-value="pricing.platform || ''"
              @update:model-value="(val: string) => changeRow(rowIndex, (row) => ({ ...row, platform: val }))"
              placeholder="anthropic"
            />
          </Field>
          <Field label="计费模式">
            <Select
              :model-value="pricing.billing_mode || 'token'"
              @update:model-value="(val: string) => changeRow(rowIndex, (row) => ({ ...row, billing_mode: val }))"
            >
              <option value="token">Token</option>
              <option value="per_request">按请求</option>
              <option value="image">图片</option>
            </Select>
          </Field>
          <div class="md:col-span-2">
            <Field label="模型列表" help="逗号或换行分隔">
              <Textarea
                rows="2"
                :model-value="(pricing.models || []).join('\n')"
                @update:model-value="(val: string) => changeRow(rowIndex, (row) => ({ ...row, models: uniqueList(val) }))"
              />
            </Field>
          </div>

          <Sub2ApiNumberField
            label="输入价 ($/MTok)"
            :perMTok="true"
            :value="pricing.input_price"
            @change="(input_price) => changeRow(rowIndex, (row) => ({ ...row, input_price }))"
          />
          <Sub2ApiNumberField
            label="输出价 ($/MTok)"
            :perMTok="true"
            :value="pricing.output_price"
            @change="(output_price) => changeRow(rowIndex, (row) => ({ ...row, output_price }))"
          />
          <Sub2ApiNumberField
            label="缓存写入价 ($/MTok)"
            :perMTok="true"
            :value="pricing.cache_write_price"
            @change="(cache_write_price) => changeRow(rowIndex, (row) => ({ ...row, cache_write_price }))"
          />
          <Sub2ApiNumberField
            label="缓存读取价 ($/MTok)"
            :perMTok="true"
            :value="pricing.cache_read_price"
            @change="(cache_read_price) => changeRow(rowIndex, (row) => ({ ...row, cache_read_price }))"
          />
          <Sub2ApiNumberField
            label="图片输入价 ($/MTok)"
            :perMTok="true"
            :value="pricing.image_input_price"
            @change="(image_input_price) => changeRow(rowIndex, (row) => ({ ...row, image_input_price }))"
          />
          <Sub2ApiNumberField
            label="图片输出价 ($/MTok)"
            :perMTok="true"
            :value="pricing.image_output_price"
            @change="(image_output_price) => changeRow(rowIndex, (row) => ({ ...row, image_output_price }))"
          />
          <Sub2ApiNumberField
            label="单请求价 ($)"
            :value="pricing.per_request_price"
            @change="(per_request_price) => changeRow(rowIndex, (row) => ({ ...row, per_request_price }))"
          />
        </div>

        <div class="border-t border-line-soft px-3 py-3">
          <div class="mb-2 flex items-center justify-between gap-3">
            <div class="text-[12.5px] font-semibold text-ink-muted">
              Token 区间价
            </div>
            <Button
              size="sm"
              variant="secondary"
              @click="changeRow(rowIndex, (row) => ({ ...row, intervals: [...(row.intervals || []), emptyInterval()] }))"
            >
              <Plus :size="14" />
              添加区间
            </Button>
          </div>
          <template v-if="(pricing.intervals || []).length">
            <div class="divide-y divide-line-soft">
              <div
                v-for="(interval, intervalIndex) in (pricing.intervals || [])"
                :key="interval.id ?? `interval-${intervalIndex}`"
                class="grid gap-2 py-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6"
              >
                <Sub2ApiNumberField
                  label="min_tokens"
                  :integer="true"
                  :value="interval.min_tokens"
                  @change="(min_tokens) => changeInterval(rowIndex, intervalIndex, (item) => ({ ...item, min_tokens: min_tokens ?? 0 }))"
                />
                <Sub2ApiNumberField
                  label="max_tokens"
                  :integer="true"
                  :value="interval.max_tokens"
                  @change="(max_tokens) => changeInterval(rowIndex, intervalIndex, (item) => ({ ...item, max_tokens }))"
                />
                <Field label="区间名称">
                  <Input
                    :model-value="interval.tier_label || ''"
                    @update:model-value="(val: string) => changeInterval(rowIndex, intervalIndex, (item) => ({ ...item, tier_label: val }))"
                  />
                </Field>
                <Sub2ApiNumberField
                  label="输入价 ($/MTok)"
                  :perMTok="true"
                  :value="interval.input_price"
                  @change="(input_price) => changeInterval(rowIndex, intervalIndex, (item) => ({ ...item, input_price }))"
                />
                <Sub2ApiNumberField
                  label="输出价 ($/MTok)"
                  :perMTok="true"
                  :value="interval.output_price"
                  @change="(output_price) => changeInterval(rowIndex, intervalIndex, (item) => ({ ...item, output_price }))"
                />
                <Sub2ApiNumberField
                  label="缓存写入价 ($/MTok)"
                  :perMTok="true"
                  :value="interval.cache_write_price"
                  @change="(cache_write_price) => changeInterval(rowIndex, intervalIndex, (item) => ({ ...item, cache_write_price }))"
                />
                <Sub2ApiNumberField
                  label="缓存读取价 ($/MTok)"
                  :perMTok="true"
                  :value="interval.cache_read_price"
                  @change="(cache_read_price) => changeInterval(rowIndex, intervalIndex, (item) => ({ ...item, cache_read_price }))"
                />
                <Sub2ApiNumberField
                  label="单请求价 ($)"
                  :value="interval.per_request_price"
                  @change="(per_request_price) => changeInterval(rowIndex, intervalIndex, (item) => ({ ...item, per_request_price }))"
                />
                <Sub2ApiNumberField
                  label="排序"
                  :integer="true"
                  :value="interval.sort_order"
                  @change="(sort_order) => changeInterval(rowIndex, intervalIndex, (item) => ({ ...item, sort_order: sort_order ?? 0 }))"
                />
                <div class="flex items-end pb-0.5">
                  <button
                    type="button"
                    :class="iconButtonClass"
                    title="移除价格区间"
                    aria-label="移除价格区间"
                    @click="changeRow(rowIndex, (row) => ({ ...row, intervals: (row.intervals || []).filter((_, index) => index !== intervalIndex) }))"
                  >
                    <Trash2 :size="15" />
                  </button>
                </div>
              </div>
            </div>
          </template>
          <template v-else>
            <div class="py-3 text-[12.5px] text-ink-soft">
              未配置区间价
            </div>
          </template>
        </div>
      </section>
    </template>
    <template v-else>
      <div class="border border-dashed border-line px-4 py-8 text-center text-sm text-ink-muted">
        暂无模型定价
      </div>
    </template>

    <Button
      variant="secondary"
      @click="emit('change', [...value, emptyPricing()])"
    >
      <Plus :size="15" />
      添加定价规则
    </Button>
  </div>
</template>
