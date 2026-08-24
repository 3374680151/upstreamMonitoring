<script setup lang="ts">
import { computed } from "vue";

interface TabItem {
  id: string;
  label: string;
}
interface Props {
  items: TabItem[];
  label: string;
}
defineProps<Props>();
const model = defineModel<string>({ required: true });

const selected = computed(() => (item: TabItem) => item.id === model.value);
</script>

<template>
  <div
    role="tablist"
    :aria-label="label"
    class="inline-flex min-h-9 gap-0.5 rounded-[var(--radius-sm)] border border-line bg-sunken p-0.5"
  >
    <button
      v-for="item in items"
      :key="item.id"
      type="button"
      role="tab"
      :aria-selected="selected(item)"
      :aria-controls="`tab-panel-${item.id}`"
      :class="[
        'min-h-7 shrink-0 rounded-[5px] px-3 text-[12.5px] font-medium transition-[background-color,color] duration-[var(--motion-base)]',
        selected(item)
          ? 'bg-panel text-ink-strong shadow-[var(--shadow-hairline)]'
          : 'text-ink-muted hover:text-ink-strong',
      ]"
      @click="model = item.id"
    >
      {{ item.label }}
    </button>
  </div>
</template>
