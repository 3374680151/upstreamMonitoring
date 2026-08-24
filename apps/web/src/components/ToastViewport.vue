<script setup lang="ts">
import { CheckCircle2, AlertTriangle, Info, X } from "lucide-vue-next";
import { useToastState, dismissToast } from "@/composables/useToast";

const { toasts } = useToastState();

const kindStyles: Record<string, { wrap: string; role: string }> = {
  success: { wrap: "border-accent/35 bg-success-bg text-success-fg", role: "status" },
  error: { wrap: "border-danger-fg/35 bg-danger-bg text-danger-fg", role: "alert" },
  info: { wrap: "border-line bg-panel text-ink", role: "status" },
};
</script>

<template>
  <div
    class="pointer-events-none fixed inset-x-3 bottom-3 z-[100] flex flex-col items-stretch gap-2 sm:inset-x-auto sm:right-4 sm:bottom-4 sm:w-[min(380px,calc(100vw-2rem))]"
    aria-live="polite"
  >
    <div
      v-for="t in toasts"
      :key="t.id"
      :role="kindStyles[t.kind].role"
      :class="[
        'upstream-pop pointer-events-auto flex items-start gap-2 rounded-[var(--radius-md)] border px-3.5 py-2.5 text-[13px] font-medium shadow-[var(--shadow-pop)] backdrop-blur-md',
        kindStyles[t.kind].wrap,
      ]"
    >
      <span class="mt-0.5 shrink-0" aria-hidden="true">
        <CheckCircle2 v-if="t.kind === 'success'" :size="15" />
        <AlertTriangle v-else-if="t.kind === 'error'" :size="15" />
        <Info v-else :size="15" />
      </span>
      <span class="min-w-0 flex-1 break-words">{{ t.message }}</span>
      <button
        type="button"
        class="-mr-1 shrink-0 rounded p-0.5 opacity-60 transition-opacity duration-[var(--motion-fast)] hover:opacity-100"
        aria-label="关闭提示"
        @click="dismissToast(t.id)"
      >
        <X :size="13" />
      </button>
    </div>
  </div>
</template>
