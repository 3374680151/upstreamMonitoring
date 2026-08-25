<script setup lang="ts">
import { onUnmounted, watch } from "vue";

interface Props {
  open: boolean;
  title: string;
  subtitle?: string;
  wide?: boolean;
}
const props = defineProps<Props>();
const emit = defineEmits<{ close: [] }>();

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") emit("close");
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      document.addEventListener("keydown", onKeydown);
      const prevOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      cleanup = () => {
        document.removeEventListener("keydown", onKeydown);
        document.body.style.overflow = prevOverflow;
      };
    } else {
      cleanup?.();
      cleanup = null;
    }
  },
);

let cleanup: (() => void) | null = null;
onUnmounted(() => {
  cleanup?.();
  cleanup = null;
});
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-overlay px-4 py-6 backdrop-blur-[3px] md:px-8 md:py-10"
      @mousedown.self="emit('close')"
    >
      <div
        role="dialog"
        aria-modal="true"
        :aria-label="title"
        :class="[
          'upstream-pop my-auto w-full overflow-hidden rounded-[var(--radius-xl)] border border-line bg-panel shadow-[var(--shadow-floating)]',
          wide ? 'max-w-5xl' : 'max-w-xl',
        ]"
      >
        <div class="flex items-start justify-between gap-4 border-b border-line bg-panel-soft px-5 py-4">
          <div class="min-w-0">
            <h3 class="t-title font-serif tracking-[-0.01em]">{{ title }}</h3>
            <p v-if="subtitle" class="mt-1 text-[12.5px] leading-relaxed text-ink-muted">
              {{ subtitle }}
            </p>
          </div>
          <button
            type="button"
            class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--radius-sm)] text-[15px] leading-none text-ink-muted transition-colors duration-[var(--motion-fast)] hover:bg-sunken hover:text-ink-strong"
            aria-label="关闭"
            @click="emit('close')"
          >
            ×
          </button>
        </div>
        <div class="px-5 py-5">
          <slot />
        </div>
      </div>
    </div>
  </Teleport>
</template>
