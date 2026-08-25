<script setup lang="ts">
interface Props {
  title: string;
  description?: string;
  dense?: boolean;
}
defineProps<Props>();
</script>

<template>
  <div
    :class="[
      'flex flex-col items-center gap-1.5 text-center',
      dense ? 'py-8' : 'py-12',
    ]"
  >
    <div class="font-serif text-[15px] font-semibold text-ink-strong">
      {{ title }}
    </div>
    <p
      v-if="description"
      class="max-w-sm text-[12.5px] leading-relaxed text-ink-muted"
    >
      {{ description }}
    </p>
    <div v-if="$slots.action" class="mt-2">
      <slot name="action" />
    </div>
  </div>
</template>
