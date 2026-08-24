<script setup lang="ts">
import { computed } from "vue";
import { Field, Input } from "@/components/ui";
import { sub2ApiMTokToPerToken, sub2ApiPerTokenToMTok } from "@/lib/sub2apiChannel";

interface Props {
  label: string;
  value?: number | null;
  integer?: boolean;
  perMTok?: boolean;
}
const props = withDefaults(defineProps<Props>(), {
  value: null,
  integer: false,
  perMTok: false,
});
const emit = defineEmits<{ change: [value: number | null] }>();

const displayValue = computed(() =>
  props.perMTok ? sub2ApiPerTokenToMTok(props.value) : props.value,
);

function nullableNumber(value: string): number | null {
  if (value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function onInput(val: any) {
  const next = nullableNumber(String(val ?? ""));
  emit("change", props.perMTok ? sub2ApiMTokToPerToken(next) : next);
}
</script>

<template>
  <Field :label="label">
    <Input
      type="number"
      :step="integer ? 1 : 'any'"
      :min="perMTok ? 0 : undefined"
      :model-value="displayValue ?? ''"
      @update:model-value="onInput"
      class="tabular-nums"
    />
  </Field>
</template>
