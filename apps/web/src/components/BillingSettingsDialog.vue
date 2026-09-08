<script setup lang="ts">
/**
 * 计费核对设置弹窗 — GET/PUT /api/billing-audit/settings。
 * push_on_red 开启后，每轮核对新增红色异常会按推送配置发邮件/企微。
 */
import { onMounted, reactive, ref } from "vue";
import { Button, Field, Input, Modal, SwitchRow } from "@/components/ui";
import { api } from "@/lib/api";
import type { BillingAuditSettings } from "@/lib/types";
import { useToast } from "@/composables/useToast";

const toast = useToast();

interface Props {
  open: boolean;
}
defineProps<Props>();
const emit = defineEmits<{ close: [] }>();

const saving = ref(false);
const loaded = ref(false);
const form = reactive<BillingAuditSettings>({
  enabled: false,
  interval_minutes: 5,
  tolerance_percent: 1,
  match_window_seconds: 300,
  retention_days: 30,
  quota_per_unit: 500000,
  push_on_red: false,
});

async function load(): Promise<void> {
  try {
    const res = await api.billingSettings();
    Object.assign(form, res.data || {});
    loaded.value = true;
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "设置读取失败");
  }
}

async function save(): Promise<void> {
  if (saving.value || !loaded.value) return;
  saving.value = true;
  try {
    const res = await api.saveBillingSettings({
      enabled: form.enabled,
      push_on_red: form.push_on_red,
      interval_minutes: Number(form.interval_minutes) || 5,
      tolerance_percent: Number(form.tolerance_percent),
      match_window_seconds: Number(form.match_window_seconds) || 300,
      retention_days: Number(form.retention_days) || 0,
      quota_per_unit: Number(form.quota_per_unit) || 500000,
    });
    Object.assign(form, res.data || {});
    toast.success("设置已保存");
    emit("close");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "设置保存失败");
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <Modal
    :open="open"
    title="计费核对设置"
    subtitle="开关与轮询间隔保存后立即生效（下一分钟内的调度 tick 会读取新值）。"
    @close="emit('close')"
  >
    <div class="flex flex-col gap-3">
      <SwitchRow
        label="启用计费核对"
        :checked="form.enabled"
        @update:checked="form.enabled = $event"
      />
      <div class="grid gap-2.5 sm:grid-cols-2">
        <Field label="轮询间隔（分钟）" help="5–1440">
          <Input v-model="form.interval_minutes" inputmode="numeric" />
        </Field>
        <Field label="偏差容差（%）" help="0–50，默认 1%">
          <Input v-model="form.tolerance_percent" inputmode="decimal" />
        </Field>
        <Field label="日志匹配时间窗（秒）" help="60–3600，默认 300">
          <Input v-model="form.match_window_seconds" inputmode="numeric" />
        </Field>
        <Field label="记录保留天数" help="0 = 永久保留">
          <Input v-model="form.retention_days" inputmode="numeric" />
        </Field>
        <Field label="Quota 兑美元基准" help="$1 = 该数值的 quota，NewAPI 默认 500000">
          <Input v-model="form.quota_per_unit" inputmode="numeric" />
        </Field>
      </div>
      <SwitchRow
        label="出现红记录时推送提醒"
        :checked="form.push_on_red"
        @update:checked="form.push_on_red = $event"
      />
      <div class="flex justify-end gap-2 pt-1">
        <Button variant="secondary" @click="emit('close')">取消</Button>
        <Button variant="brand" :loading="saving" @click="save">保存</Button>
      </div>
    </div>
  </Modal>
</template>
