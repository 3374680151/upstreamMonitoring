<script setup lang="ts">
interface Props {
  label: string;
  checked: boolean;
}
const props = defineProps<Props>();
const emit = defineEmits<{ "update:checked": [v: boolean] }>();

function toggle() {
  emit("update:checked", !props.checked);
}
</script>

<template>
  <button
    type="button"
    role="switch"
    :aria-checked="checked"
    :class="[
      'flex w-full items-center justify-between gap-3 rounded-[var(--radius-md)] border px-3 py-2.5 text-left transition-[border-color,background-color] duration-[var(--motion-base)]',
      checked
        ? 'border-accent/40 bg-accent-soft/60'
        : 'border-line bg-panel-soft hover:border-line-strong',
    ]"
    @click="toggle"
  >
    <span class="text-[13.5px] font-medium text-ink">{{ label }}</span>
    <span
      :class="[
        'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-[var(--motion-base)]',
        checked ? 'bg-accent' : 'bg-sunken-active',
      ]"
      aria-hidden="true"
    >
      <span
        :class="[
          'inline-block h-4 w-4 transform rounded-full bg-paper shadow-[var(--shadow-pop)] transition-transform duration-[var(--motion-base)]',
          checked ? 'translate-x-4' : 'translate-x-0.5',
        ]"
      />
    </span>
  </button>
</template>
