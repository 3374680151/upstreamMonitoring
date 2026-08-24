<script setup lang="ts">
interface Props {
  title: string;
  subtitle?: string;
  large?: boolean;
}
defineProps<Props>();
</script>

<template>
  <div class="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
    <div class="flex items-end gap-4">
      <span
        class="mb-1 h-10 w-[2px] shrink-0 rounded-full bg-accent"
        aria-hidden="true"
      />
      <div class="min-w-0">
        <h1
          :class="[
            'font-serif font-semibold tracking-[-0.02em] text-ink-strong',
            large ? 'text-[28px] leading-[1.1]' : 'text-[24px] leading-[1.15]',
          ]"
        >
          {{ title }}
        </h1>
        <p v-if="subtitle" class="mt-2 max-w-3xl text-[13px] leading-relaxed text-ink-muted">
          {{ subtitle }}
        </p>
      </div>
    </div>
    <div v-if="$slots.action" class="w-full min-w-0 shrink-0 sm:w-auto">
      <slot name="action" />
    </div>
  </div>
</template>
