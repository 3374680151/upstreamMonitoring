<script setup lang="ts">
import { ref } from "vue";
import { api, setConsoleToken } from "@/lib/api";
import { Button, Input } from "@/components/ui";
import { errorText, useToast } from "@/composables/useToast";

const emit = defineEmits<{ success: [] }>();
const toast = useToast();

const password = ref("");
const error = ref("");
const busy = ref(false);

async function submit(): Promise<void> {
  if (!password.value) {
    error.value = "请输入密码";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const resp = await api.login(password.value);
    if (!resp.success || !resp.token) {
      throw new Error(resp.message || "登录失败");
    }
    setConsoleToken(resp.token);
    toast.success("登录成功");
    emit("success");
  } catch (err) {
    error.value = errorText(err, "登录失败");
    toast.error(error.value);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center px-4">
    <div
      class="w-full max-w-[400px] overflow-hidden rounded-[var(--radius-xl)] border border-line bg-panel shadow-[var(--shadow-floating)]"
    >
      <div class="border-b border-line-soft bg-panel-soft px-7 py-6">
        <div class="flex items-center gap-3">
          <span
            class="inline-flex h-10 w-10 items-center justify-center rounded-[10px] font-serif text-[17px] font-semibold text-ink-on-accent shadow-[var(--shadow-pop)]"
            style="background-image: linear-gradient(135deg, #2c8a5a 0%, #1f6e47 100%)"
          >
            U
          </span>
          <div class="leading-tight">
            <div class="font-serif text-[16px] font-semibold tracking-[-0.01em] text-ink-strong">
              Upstream 控制台
            </div>
            <div class="t-micro mt-0.5">访问受密码保护</div>
          </div>
        </div>
      </div>

      <form class="flex flex-col gap-6 px-7 py-6 md:gap-8" @submit.prevent="submit">
        <label class="flex flex-col gap-1.5">
          <span class="t-small font-medium text-ink-muted">控制台密码</span>
          <Input
            v-model="password"
            type="password"
            autofocus
            autocomplete="current-password"
            placeholder="在服务端 .env 中设置"
          />
        </label>

        <div
          v-if="error"
          class="rounded-[var(--radius-sm)] border border-danger-fg/25 bg-danger-bg px-3 py-2 text-[12.5px] text-danger-fg"
        >
          {{ error }}
        </div>

        <Button
          type="submit"
          variant="brand"
          class="h-9 w-full text-[13.5px]"
          :loading="busy"
        >
          {{ busy ? "登录中..." : "登录" }}
        </Button>

        <p class="text-[11.5px] leading-relaxed text-ink-soft">
          密码在服务端通过环境变量 <code class="font-mono">CONSOLE_PASSWORD</code> 设置。留空该变量则不启用登录（仅建议本地/内网直连时）。
        </p>
      </form>
    </div>
  </div>
</template>
