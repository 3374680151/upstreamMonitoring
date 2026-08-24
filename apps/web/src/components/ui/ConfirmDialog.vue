<script setup lang="ts">
import Modal from "./Modal.vue";
import Button from "./Button.vue";

interface Props {
  open: boolean;
  title: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  busy?: boolean;
  error?: string;
}
withDefaults(defineProps<Props>(), {
  confirmLabel: "确认",
  cancelLabel: "取消",
  danger: false,
  busy: false,
  error: "",
});
const emit = defineEmits<{ confirm: []; cancel: [] }>();
</script>

<template>
  <Modal :open="open" :title="title" @close="busy ? undefined : emit('cancel')">
    <div class="flex flex-col gap-4">
      <div class="text-[13.5px] leading-relaxed text-ink">
        <slot />
      </div>
      <div
        v-if="error"
        class="rounded-[var(--radius-sm)] border border-danger-fg/25 bg-danger-bg px-3 py-2 text-[12.5px] text-danger-fg"
      >
        {{ error }}
      </div>
      <div class="flex justify-end gap-2">
        <Button variant="secondary" :disabled="busy" @click="emit('cancel')">
          {{ cancelLabel }}
        </Button>
        <Button
          :variant="danger ? 'danger' : 'primary'"
          :loading="busy"
          @click="emit('confirm')"
        >
          {{ confirmLabel }}
        </Button>
      </div>
    </div>
  </Modal>
</template>
