<script setup lang="ts">
const model = defineModel<any>();
</script>

<template>
  <select
    v-model="model"
    class="h-8 w-full cursor-pointer appearance-none rounded-[var(--radius-sm)] border border-line bg-panel px-2.5 pr-7 text-[13px] text-ink outline-none transition-[border-color,box-shadow] duration-[var(--motion-base)] [background-image:var(--select-chevron)] [background-position:right_8px_center] [background-repeat:no-repeat] [background-size:12px_12px] hover:border-line-strong focus:border-accent focus:shadow-[0_0_0_3px_var(--color-accent-ring)]"
  >
    <slot />
  </select>
</template>
