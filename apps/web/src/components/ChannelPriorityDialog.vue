<script setup lang="ts">
import { ref, watch } from "vue";
import type { Channel } from "@/lib/types";
import { parseChannelPriority } from "@/lib/channelPriority";
import { Button, Field, Input, Modal } from "@/components/ui";

interface Props {
  open: boolean;
  channel: Channel | null;
  onSubmit: (priority: number) => Promise<void>;
}
const props = defineProps<Props>();
const emit = defineEmits<{ close: [] }>();

const priorityInput = ref("0");
const saving = ref(false);
const error = ref("");

watch(
  () => [props.open, props.channel?.id] as const,
  () => {
    if (!props.open) return;
    priorityInput.value = String(props.channel?.priority ?? 0);
    error.value = "";
  },
  { immediate: true },
);

async function save() {
  const priority = parseChannelPriority(priorityInput.value);
  if (priority === null) {
    error.value = "请输入有效的整数优先级";
    return;
  }
  saving.value = true;
  error.value = "";
  try {
    await props.onSubmit(priority);
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
    :title="`编辑优先级 · ${channel?.name || channel?.id || '渠道'}`"
    subtitle="仅调整主站调度优先级，其他渠道配置保持不变"
    @close="emit('close')"
  >
    <Field label="优先级 priority" help="数值越大越优先被调度">
      <Input type="number" :step="1" v-model="priorityInput" />
    </Field>
    <div
      v-if="error"
      class="mt-3 rounded-[var(--radius-sm)] border border-danger-fg/25 bg-danger-bg px-3 py-2 text-[12.5px] text-danger-fg"
    >
      {{ error }}
    </div>
    <div class="mt-5 flex justify-end gap-2">
      <Button variant="secondary" :disabled="saving" @click="emit('close')">
        取消
      </Button>
      <Button :loading="saving" @click="save">
        保存优先级
      </Button>
    </div>
  </Modal>
</template>
