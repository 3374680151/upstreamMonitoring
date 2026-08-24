<script setup lang="ts">
import { computed } from "vue";
import { formatRate, successTone } from "@/lib/perf";

interface Props {
  values: number[];
}
const props = defineProps<Props>();

const bars = computed(() => {
  const visibleValues = props.values.slice(-12);
  return visibleValues.map((value, index) => {
    const number = Number(value);
    const height = Math.max(3, Math.min(16, (number / 100) * 16));
    const tone = successTone(number);
    const color =
      tone === "success"
        ? "bg-[var(--color-success-fg)]"
        : tone === "warning"
          ? "bg-[var(--color-warning-fg)]"
          : tone === "danger"
            ? "bg-[var(--color-danger-fg)]"
            : "bg-sunken-active";
    return { index, height, color, number };
  });
});

const ariaLabel = computed(() => {
  const visibleValues = props.values.slice(-12);
  return `最近时间桶成功率：${visibleValues.map(formatRate).join("、")}`;
});
</script>

<template>
  <span
    class="inline-flex h-4 items-end gap-0.5"
    title="最近时间桶成功率"
    role="img"
    :aria-label="ariaLabel"
  >
    <span
      v-for="bar in bars"
      :key="bar.index"
      :class="['w-1.5 rounded-sm', bar.color]"
      :style="{ height: bar.height + 'px' }"
      :title="formatRate(bar.number)"
      aria-hidden="true"
    />
  </span>
</template>
