<script setup lang="ts">
import { computed } from "vue";
import { ArrowRight, TrendingDown, TrendingUp } from "lucide-vue-next";

interface Props {
  message?: string | null;
}
const props = defineProps<Props>();

interface ParsedValue {
  matched: boolean;
  text: string;
  prefix?: string;
  rawOld?: string;
  rawNew?: string;
  suffix?: string;
  tone?: string;
  icon?: "up" | "down" | "right";
}

const parsed = computed<ParsedValue>(() => {
  const text = props.message || "-";
  const match = text.match(/^(.*?)([\d.]+)\s*(?:->|→|=>)\s*([\d.]+)(.*)$/);
  if (!match) return { matched: false, text };
  const [, prefix, rawOld, rawNew, suffix] = match;
  const oldNum = Number(rawOld);
  const newNum = Number(rawNew);
  const up =
    Number.isFinite(oldNum) && Number.isFinite(newNum) && newNum > oldNum;
  const down =
    Number.isFinite(oldNum) && Number.isFinite(newNum) && newNum < oldNum;
  const tone = down
    ? "text-success-fg"
    : up
      ? "text-danger-fg"
      : "text-ink-strong";
  const icon = down ? "down" : up ? "up" : "right";
  return {
    matched: true,
    text,
    prefix: prefix.trim(),
    rawOld,
    rawNew,
    suffix: suffix.trim(),
    tone,
    icon,
  };
});
</script>

<template>
  <span v-if="!parsed.matched" class="text-ink-muted">{{ parsed.text }}</span>
  <span v-else class="inline-flex flex-wrap items-center gap-1.5">
    <span v-if="parsed.prefix" class="text-ink-muted">{{ parsed.prefix }}</span>
    <span class="tabular text-ink-soft line-through decoration-line">{{ parsed.rawOld }}</span>
    <TrendingDown
      v-if="parsed.icon === 'down'"
      :size="12"
      :class="parsed.tone"
      aria-hidden="true"
    />
    <TrendingUp
      v-else-if="parsed.icon === 'up'"
      :size="12"
      :class="parsed.tone"
      aria-hidden="true"
    />
    <ArrowRight
      v-else
      :size="12"
      :class="parsed.tone"
      aria-hidden="true"
    />
    <span class="font-semibold tabular" :class="parsed.tone">{{ parsed.rawNew }}</span>
    <span v-if="parsed.suffix" class="text-ink-muted">{{ parsed.suffix }}</span>
  </span>
</template>
