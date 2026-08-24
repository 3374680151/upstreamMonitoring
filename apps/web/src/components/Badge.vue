<script setup lang="ts">
type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "brand";

interface Props {
  tone?: Tone;
  dot?: boolean;
  title?: string;
}
const props = withDefaults(defineProps<Props>(), {
  tone: "neutral",
  dot: false,
});

const toneClass: Record<Tone, string> = {
  neutral: "bg-sunken text-ink-muted border-line",
  success: "bg-success-bg text-success-fg border-transparent",
  warning: "bg-warning-bg text-warning-fg border-transparent",
  danger: "bg-danger-bg text-danger-fg border-transparent",
  info: "bg-info-bg text-info-fg border-transparent",
  brand: "bg-accent text-ink-on-accent border-transparent",
};
</script>

<template>
  <span
    :class="[
      'inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border px-2 py-0.5 text-[11px] font-medium tracking-[0.01em] whitespace-nowrap leading-none',
      toneClass[props.tone],
    ]"
    :title="title"
  >
    <span
      v-if="dot"
      class="h-1.5 w-1.5 shrink-0 rounded-full bg-current"
      aria-hidden="true"
    />
    <slot />
  </span>
</template>
