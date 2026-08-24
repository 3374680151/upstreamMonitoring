<script setup lang="ts">
import { computed } from "vue";

type StatTone = "brand" | "neutral" | "danger" | "info" | "warning";

interface Props {
  label: string;
  tone?: StatTone;
  accent?: boolean;
}
const props = withDefaults(defineProps<Props>(), {
  tone: "brand",
  accent: true,
});

const toneStyles: Record<StatTone, { bar: string; chip: string }> = {
  brand: { bar: "bg-accent", chip: "bg-success-bg text-success-fg" },
  neutral: { bar: "bg-ink-faint", chip: "bg-sunken text-ink-muted" },
  danger: { bar: "bg-danger-fg", chip: "bg-danger-bg text-danger-fg" },
  info: { bar: "bg-info-fg", chip: "bg-info-bg text-info-fg" },
  warning: { bar: "bg-warning-fg", chip: "bg-warning-bg text-warning-fg" },
};

const styles = computed(() => toneStyles[props.tone]);
</script>

<template>
  <div
    class="group relative overflow-hidden rounded-[var(--radius-md)] border border-line bg-panel p-3.5 shadow-[var(--shadow-hairline)] transition-[border-color,box-shadow] duration-[var(--motion-base)] hover:border-line-strong hover:shadow-[var(--shadow-pop)] md:p-4"
  >
    <span
      v-if="accent"
      :class="['absolute top-3 bottom-3 left-0 w-[2px] rounded-full', styles.bar]"
      aria-hidden="true"
    />
    <div :class="['relative flex items-start justify-between gap-3', accent ? 'pl-2.5' : '']">
      <div class="min-w-0">
        <div class="t-micro">{{ label }}</div>
        <div class="mt-1.5 font-serif text-[24px] leading-none tabular tracking-[-0.02em] text-ink-strong md:text-[26px]">
          <slot name="value" />
        </div>
        <div v-if="$slots.hint" class="mt-1.5 text-[11.5px] leading-tight text-ink-soft">
          <slot name="hint" />
        </div>
      </div>
      <span
        v-if="$slots.icon"
        :class="[
          'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-sm)] transition-transform duration-[var(--motion-base)] group-hover:scale-105',
          styles.chip,
        ]"
        aria-hidden="true"
      >
        <slot name="icon" />
      </span>
    </div>
  </div>
</template>
