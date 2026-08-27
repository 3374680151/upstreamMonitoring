<script setup lang="ts">
import { ref, watch } from "vue";
import { Button, Field, Input, Modal } from "@/components/ui";
import { errorText } from "@/composables/useToast";

interface Props {
  open: boolean;
  /** 当前主站名称，仅用于展示 */
  siteName?: string;
  /** 验证通过后要续跑的动作：key=全量渠道 key 刷新，ratio=全量倍率刷新 */
  intent?: "key" | "ratio";
  /** 发起验证；失败时 throw Error(message)，弹窗内保留错误便于改码重试 */
  onSubmit: (code: string) => Promise<void>;
}
const props = defineProps<Props>();
const emit = defineEmits<{ close: [] }>();

const securityCode = ref("");
const verifying = ref(false);
const error = ref("");

watch(
  () => props.open,
  () => {
    if (!props.open) return;
    securityCode.value = "";
    error.value = "";
  },
  { immediate: true },
);

async function save() {
  const code = securityCode.value.trim();
  if (!code) {
    error.value = "请输入主站当前 2FA 验证码";
    return;
  }
  verifying.value = true;
  error.value = "";
  try {
    await props.onSubmit(code);
    emit("close");
  } catch (err) {
    error.value = errorText(err, "主站安全验证失败");
  } finally {
    verifying.value = false;
  }
}

const intentText = { key: "渠道 key 刷新", ratio: "倍率刷新" } as const;
</script>

<template>
  <Modal
    :open="open"
    title="重新验证主站 2FA"
    :subtitle="`${siteName ? `主站「${siteName}」` : ''}需要安全验证后才能继续${
      intentText[intent ?? 'key']
    }`"
    @close="emit('close')"
  >
    <Field
      label="主站 2FA 验证码"
      help="打开验证器 App（如 Google Authenticator）获取当前 6 位动态码；验证通过后会自动继续刷新"
    >
      <Input
        v-model="securityCode"
        class="min-w-0 flex-1"
        inputmode="numeric"
        autocomplete="one-time-code"
        placeholder="当前验证码"
        autofocus
        @keyup.enter="save"
      />
    </Field>
    <div
      v-if="error"
      class="mt-3 rounded-[var(--radius-sm)] border border-danger-fg/25 bg-danger-bg px-3 py-2 text-[12.5px] text-danger-fg"
    >
      {{ error }}
    </div>
    <div class="mt-5 flex justify-end gap-2">
      <Button variant="secondary" :disabled="verifying" @click="emit('close')">
        取消
      </Button>
      <Button :loading="verifying" @click="save">验证并继续</Button>
    </div>
  </Modal>
</template>
