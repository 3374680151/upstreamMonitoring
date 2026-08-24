<script setup lang="ts">
interface Props {
  label: string;
  help?: string;
}
defineProps<Props>();
</script>

<template>
  <label class="flex flex-col gap-1.5">
    <span class="t-small font-medium text-ink-muted">{{ label }}</span>
    <slot />
    <span v-if="help" class="text-[11.5px] leading-relaxed text-ink-soft">
      {{ help }}
    </span>
  </label>
</template>
