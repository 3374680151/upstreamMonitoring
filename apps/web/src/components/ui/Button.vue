<script setup lang="ts">
import { computed } from "vue";
import Spinner from "./Spinner.vue";

interface Props {
  variant?: "primary" | "secondary" | "danger" | "ghost" | "brand";
  size?: "sm" | "md";
  loading?: boolean;
  disabled?: boolean;
}
const props = withDefaults(defineProps<Props>(), {
  variant: "primary",
  size: "md",
  loading: false,
  disabled: false,
});

const variantStyles: Record<string, string> = {
  primary: "bg-ink-strong text-ink-on-accent border border-ink-strong hover:bg-ink-strong/90",
  brand: "bg-accent text-ink-on-accent border border-accent hover:bg-accent-hover shadow-[var(--shadow-pop)]",
  secondary: "bg-panel text-ink border border-line hover:bg-sunken-hover hover:border-line-strong",
  danger: "bg-danger-bg text-danger-fg border border-danger-fg/30 hover:bg-danger-bg/70 hover:border-danger-fg/55",
  ghost: "bg-transparent text-ink-muted border border-transparent hover:bg-sunken hover:text-ink-strong",
};
const sizeStyles: Record<string, string> = {
  sm: "h-7 px-2.5 text-[12.5px]",
  md: "h-8 px-3 text-[13px]",
};
const classes = computed(() => [
  "inline-flex items-center justify-center gap-1.5 rounded-[var(--radius-sm)] font-medium tracking-[0.01em] transition-[background-color,border-color,transform,box-shadow] duration-[var(--motion-base)] ease-[var(--ease-out-quart)] active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50",
  sizeStyles[props.size],
  variantStyles[props.variant],
]);
</script>

<template>
  <button
    type="button"
    :aria-busy="loading || undefined"
    :class="classes"
    :disabled="disabled || loading"
  >
    <Spinner v-if="loading" />
    <slot />
  </button>
</template>
