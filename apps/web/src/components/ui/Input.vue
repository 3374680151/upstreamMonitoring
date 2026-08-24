<script setup lang="ts">
const model = defineModel<any>();
</script>

<template>
  <input
    v-model="model"
    class="h-8 w-full rounded-[var(--radius-sm)] border border-line bg-panel px-2.5 text-[13px] text-ink outline-none transition-[border-color,box-shadow] duration-[var(--motion-base)] placeholder:text-ink-faint hover:border-line-strong focus:border-accent focus:shadow-[0_0_0_3px_var(--color-accent-ring)]"
  />
</template>
