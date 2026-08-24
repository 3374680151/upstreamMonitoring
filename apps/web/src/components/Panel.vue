<script setup lang="ts">
interface Props {
  title: string;
  subtitle?: string;
}
defineProps<Props>();
</script>

<template>
  <section
    class="min-h-0 min-w-0 overflow-hidden rounded-[var(--radius-lg)] border border-line bg-panel shadow-[var(--shadow-hairline)] transition-[border-color] duration-[var(--motion-base)] hover:border-line-strong"
  >
    <header class="flex flex-wrap items-start justify-between gap-3 border-b border-line-soft bg-panel-soft px-5 py-3.5">
      <div class="min-w-0">
        <h2 class="font-serif text-[15.5px] font-semibold tracking-[-0.01em] text-ink-strong">
          {{ title }}
        </h2>
        <p v-if="subtitle" class="mt-0.5 text-[12px] leading-relaxed text-ink-muted">
          {{ subtitle }}
        </p>
      </div>
      <div v-if="$slots.action" class="min-w-0 max-w-full shrink-0 max-sm:w-full max-sm:shrink">
        <slot name="action" />
      </div>
    </header>
    <div class="flex min-h-0 min-w-0 flex-1 flex-col p-4 md:p-5">
      <slot />
    </div>
  </section>
</template>
