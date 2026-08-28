<script setup lang="ts">
import { computed } from "vue";
import Badge from "@/components/Badge.vue";
import { Button, Modal } from "@/components/ui";
import {
  cancelLoginAssist,
  loginAssistState,
  retryLoginAssistNow,
  type LoginAssistPhase,
} from "@/composables/useLoginAssistSync";

const PHASE_BADGE: Record<LoginAssistPhase, { tone: "info" | "warning" | "success" | "danger"; text: string }> = {
  probing: { tone: "info", text: "正在探测登录态" },
  opening: { tone: "info", text: "正在打开站点页" },
  waiting: { tone: "warning", text: "等待登录" },
  success: { tone: "success", text: "登录态已同步" },
  stopped: { tone: "danger", text: "已停止" },
};

const badge = computed(() => PHASE_BADGE[loginAssistState.phase]);
const busy = computed(
  () =>
    loginAssistState.phase === "probing" ||
    loginAssistState.phase === "opening",
);
const stopped = computed(() => loginAssistState.phase === "stopped");

const bodyText = computed(() => {
  switch (loginAssistState.phase) {
    case "opening":
      return "正在为你打开站点页面，请在其中完成登录。";
    case "waiting":
      return "请在打开的站点页面完成登录。登录成功后会自动同步并保留登录态，无需回到本页操作。";
    case "probing":
      return "正在读取站点登录态，稍候…";
    case "success":
      return "已拿到最新登录态并保存，弹窗将自动关闭。";
    case "stopped":
      return loginAssistState.hint || "登录引导已停止。";
  }
  return "";
});
</script>

<template>
  <Modal
    :open="loginAssistState.open"
    title="登录态同步"
    :subtitle="loginAssistState.siteName"
    @close="cancelLoginAssist"
  >
    <div class="flex flex-col gap-3">
      <div class="flex items-center gap-2">
        <Badge :tone="badge.tone" dot>{{ badge.text }}</Badge>
        <span
          v-if="loginAssistState.attempt > 0"
          class="text-[11.5px] text-ink-muted"
        >第 {{ loginAssistState.attempt }} 次探测</span>
      </div>
      <p class="text-[13px] leading-relaxed text-ink">{{ bodyText }}</p>
      <p
        v-if="loginAssistState.hint && loginAssistState.phase !== 'stopped'"
        class="text-[12px] leading-relaxed text-ink-muted"
      >
        {{ loginAssistState.hint }}
      </p>
      <p
        v-if="loginAssistState.siteUrl"
        class="text-[12px] leading-relaxed text-ink-muted"
      >
        没有弹出站点页？
        <a
          class="font-medium text-accent hover:underline"
          :href="loginAssistState.siteUrl"
          target="_blank"
          rel="noopener"
        >手动打开「{{ loginAssistState.siteName }}」</a>
      </p>
    </div>
    <div class="mt-5 flex items-center justify-end gap-2">
      <Button variant="ghost" :disabled="false" @click="cancelLoginAssist">
        {{ stopped ? "关闭" : "取消" }}
      </Button>
      <Button
        v-if="!stopped"
        variant="brand"
        :loading="busy"
        @click="retryLoginAssistNow"
      >
        已登录，立即重试
      </Button>
    </div>
  </Modal>
</template>
