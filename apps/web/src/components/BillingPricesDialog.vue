<script setup lang="ts">
/**
 * 官方价格表管理弹窗 — 列表 + 新增/编辑 + 删除。
 * 对应 GET/PUT /api/billing-audit/prices、DELETE /api/billing-audit/prices/{model}；
 * 保存/删除由父页面执行，组件只发事件（业务组件之间不互相调用 API 之外的东西）。
 * 约定简化：组件自己调 api 域（页面只做编排传递），与其他弹窗一致保持自包含。
 */
import { onMounted, reactive, ref } from "vue";
import Badge from "@/components/Badge.vue";
import { Button, ConfirmDialog, Field, Input, Modal, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import type { OfficialModelPrice } from "@/lib/types";
import { useToast } from "@/composables/useToast";

const toast = useToast();

interface Props {
  open: boolean;
}
defineProps<Props>();
const emit = defineEmits<{ close: [] }>();

const prices = ref<OfficialModelPrice[]>([]);
const loading = ref(false);
const saving = ref(false);
const editing = ref<string | null>(null);
const deleteTarget = ref<OfficialModelPrice | null>(null);
const deleting = ref(false);

const form = reactive({
  model_name: "",
  quota_type: "per_token" as "per_token" | "per_call",
  input_usd_per_m: "",
  cached_input_usd_per_m: "",
  cache_write_usd_per_m: "",
  output_usd_per_m: "",
  price_per_call_usd: "",
});

async function load(): Promise<void> {
  loading.value = true;
  try {
    const res = await api.billingPrices();
    prices.value = res.data?.items || [];
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "价格表读取失败");
  } finally {
    loading.value = false;
  }
}

function resetForm(): void {
  editing.value = null;
  form.model_name = "";
  form.quota_type = "per_token";
  form.input_usd_per_m = "";
  form.cached_input_usd_per_m = "";
  form.cache_write_usd_per_m = "";
  form.output_usd_per_m = "";
  form.price_per_call_usd = "";
}

function startEdit(price: OfficialModelPrice): void {
  editing.value = price.model_name;
  form.model_name = price.model_name;
  form.quota_type = price.quota_type;
  form.input_usd_per_m = price.input_usd_per_m === null ? "" : String(price.input_usd_per_m);
  form.cached_input_usd_per_m = price.cached_input_usd_per_m === null ? "" : String(price.cached_input_usd_per_m);
  form.cache_write_usd_per_m = price.cache_write_usd_per_m === null ? "" : String(price.cache_write_usd_per_m);
  form.output_usd_per_m = price.output_usd_per_m === null ? "" : String(price.output_usd_per_m);
  form.price_per_call_usd = price.price_per_call_usd === null ? "" : String(price.price_per_call_usd);
}

async function save(): Promise<void> {
  if (!form.model_name.trim() || saving.value) return;
  saving.value = true;
  try {
    await api.saveBillingPrice({
      model_name: form.model_name.trim(),
      quota_type: form.quota_type,
      input_usd_per_m: form.input_usd_per_m === "" ? null : Number(form.input_usd_per_m),
      cached_input_usd_per_m: form.cached_input_usd_per_m === "" ? null : Number(form.cached_input_usd_per_m),
      cache_write_usd_per_m: form.cache_write_usd_per_m === "" ? null : Number(form.cache_write_usd_per_m),
      output_usd_per_m: form.output_usd_per_m === "" ? null : Number(form.output_usd_per_m),
      price_per_call_usd: form.price_per_call_usd === "" ? null : Number(form.price_per_call_usd),
    });
    resetForm();
    toast.success("价格已保存");
    await load();
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "价格保存失败");
  } finally {
    saving.value = false;
  }
}

async function removeConfirmed(): Promise<void> {
  const price = deleteTarget.value;
  if (!price || deleting.value) return;
  deleting.value = true;
  try {
    await api.deleteBillingPrice(price.model_name);
    if (editing.value === price.model_name) resetForm();
    toast.success("已删除");
    await load();
    deleteTarget.value = null;
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "删除失败");
  } finally {
    deleting.value = false;
  }
}

onMounted(load);
</script>

<template>
  <Modal
    :open="open"
    title="官方价格表"
    subtitle="按模型名精确匹配（USD / 1M tokens）；缺失的模型会按「无法核对」灰显示，保存后立即用于后续核对。"
    wide
    @close="emit('close')"
  >
    <div class="flex flex-col gap-4">
      <form class="grid gap-2.5 rounded-[var(--radius-md)] border border-line bg-panel-soft p-3 sm:grid-cols-3" @submit.prevent="save">
        <Field label="模型名">
          <Input v-model="form.model_name" placeholder="例如 gpt-4o" />
        </Field>
        <Field label="计费类型">
          <Select v-model="form.quota_type">
            <option value="per_token">按 token</option>
            <option value="per_call">按次</option>
          </Select>
        </Field>
        <Field label="输入价">
          <Input v-model="form.input_usd_per_m" inputmode="decimal" placeholder="2.5" />
        </Field>
        <Field label="缓存命中输入价">
          <Input v-model="form.cached_input_usd_per_m" inputmode="decimal" placeholder="1.25" />
        </Field>
        <Field label="缓存写入价">
          <Input v-model="form.cache_write_usd_per_m" inputmode="decimal" placeholder="3.75" />
        </Field>
        <Field label="输出价">
          <Input v-model="form.output_usd_per_m" inputmode="decimal" placeholder="10" />
        </Field>
        <Field label="按次价格（per_call 用）">
          <Input v-model="form.price_per_call_usd" inputmode="decimal" placeholder="0.02" />
        </Field>
        <div class="flex items-end gap-2 sm:col-span-3">
          <Button type="submit" variant="brand" :loading="saving">
            {{ editing ? "保存修改" : "新增价格" }}
          </Button>
          <Button v-if="editing" type="button" variant="secondary" @click="resetForm">取消编辑</Button>
        </div>
      </form>

      <div class="overflow-x-auto">
        <table class="w-full min-w-[720px] border-collapse text-[12.5px]">
          <thead>
            <tr class="border-b border-line text-left text-[11px] text-ink-muted">
              <th class="px-2 py-1.5 font-medium">模型</th>
              <th class="px-2 py-1.5 font-medium">类型</th>
              <th class="px-2 py-1.5 text-right font-medium">输入</th>
              <th class="px-2 py-1.5 text-right font-medium">缓存读</th>
              <th class="px-2 py-1.5 text-right font-medium">缓存写</th>
              <th class="px-2 py-1.5 text-right font-medium">输出</th>
              <th class="px-2 py-1.5 font-medium">来源</th>
              <th class="px-2 py-1.5 font-medium">更新时间</th>
              <th class="px-2 py-1.5 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="price in prices" :key="price.model_name" class="border-b border-line-soft">
              <td class="px-2 py-1.5 font-medium text-ink-strong">{{ price.model_name }}</td>
              <td class="px-2 py-1.5 text-ink-muted">{{ price.quota_type === "per_call" ? "按次" : "按量" }}</td>
              <td class="px-2 py-1.5 text-right text-ink tabular">{{ price.input_usd_per_m ?? "—" }}</td>
              <td class="px-2 py-1.5 text-right text-ink tabular">{{ price.cached_input_usd_per_m ?? "—" }}</td>
              <td class="px-2 py-1.5 text-right text-ink tabular">{{ price.cache_write_usd_per_m ?? "—" }}</td>
              <td class="px-2 py-1.5 text-right text-ink tabular">{{ price.output_usd_per_m ?? "—" }}</td>
              <td class="px-2 py-1.5">
                <Badge :tone="price.source === 'manual' ? 'info' : price.source === 'sub2api' ? 'success' : 'neutral'">
                  {{ price.source === "manual" ? "手动" : price.source === "sub2api" ? "sub2api 同步" : "内置" }}
                </Badge>
              </td>
              <td class="whitespace-nowrap px-2 py-1.5 text-ink-muted tabular">{{ fmtTime(price.updated_at) }}</td>
              <td class="px-2 py-1.5">
                <div class="flex justify-end gap-1.5">
                  <Button variant="ghost" @click="startEdit(price)">编辑</Button>
                  <Button variant="ghost" @click="deleteTarget = price">删除</Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <ConfirmDialog
      :open="deleteTarget !== null"
      title="删除官方价格"
      confirm-label="删除"
      danger
      :busy="deleting"
      @confirm="removeConfirmed"
      @cancel="deleteTarget = null"
    >
      确认删除「{{ deleteTarget?.model_name }}」的官方价格？删除后该模型的新核对会按「无法核对」灰显示，历史记录不受影响。
    </ConfirmDialog>
  </Modal>
</template>
