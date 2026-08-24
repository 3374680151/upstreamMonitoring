<script setup lang="ts">
const model = defineModel<any>();
</script>

<template>
  <textarea
    v-model="model"
    class="w-full rounded-[var(--radius-sm)] border border-line bg-panel px-3 py-2 font-mono text-[12.5px] leading-relaxed text-ink outline-none transition-[border-color,box-shadow] duration-[var(--motion-base)] placeholder:text-ink-faint hover:border-line-strong focus:border-accent focus:shadow-[0_0_0_3px_var(--color-accent-ring)]"
  />
</template>
