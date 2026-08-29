<script setup lang="ts">
/**
 * 主站同步范围选择弹窗 — 渠道页「同步主站」的范围确认层。
 * 纯展示组件：可同步候选由页面拉取并按平台识别筛过后传入，
 * 确认时回传 { scope, channelIds }，同步请求由页面发起。
 */
import { computed, ref, watch } from "vue";
import { Button, Modal, Spinner } from "@/components/ui";
import type { ChannelDiscoveryCandidate } from "@/lib/types";

type SyncScope = "all" | "recognized" | "selected";

interface Props {
  open: boolean;
  siteLabel?: string;
  candidatesLoading?: boolean;
  candidates?: ChannelDiscoveryCandidate[];
}
const props = defineProps<Props>();
const emit = defineEmits<{
  close: [];
  confirm: [payload: { scope: SyncScope; channelIds: number[] }];
}>();

const scopeChoice = ref<SyncScope>("all");
const checkedUrls = ref<Set<string>>(new Set());

watch(
  () => props.open,
  (open) => {
    if (open) {
      scopeChoice.value = "all";
      checkedUrls.value = new Set();
    }
  },
);

const scopeOptions = computed(() => {
  const options: { value: SyncScope; label: string; hint: string }[] = [
    {
      value: "all",
      label: "全部渠道",
      hint: "导入全部渠道指向的上游站点",
    },
    {
      value: "recognized",
      label: "仅识别 NewAPI / sub2api",
      hint: "只导入平台识别命中的渠道，并把本地已导入但平台不符的站点直接删除",
    },
    {
      value: "selected",
      label: "勾选渠道",
      hint: "只导入勾选渠道的上游站点，未勾选渠道的站点保持不动",
    },
  ];
  return options;
});

const candidateRows = computed(() => props.candidates || []);

const selectedChannelIds = computed(() => {
  const ids: number[] = [];
  for (const candidate of candidateRows.value) {
    if (checkedUrls.value.has(candidate.base_url)) {
      ids.push(...(candidate.channel_ids || []));
    }
  }
  return ids;
});

const allChecked = computed(
  () =>
    candidateRows.value.length > 0 &&
    candidateRows.value.every((row) => checkedUrls.value.has(row.base_url)),
);

const canConfirm = computed(
  () => scopeChoice.value !== "selected" || selectedChannelIds.value.length > 0,
);

function toggleCandidate(url: string) {
  const next = new Set(checkedUrls.value);
  if (next.has(url)) next.delete(url);
  else next.add(url);
  checkedUrls.value = next;
}

function toggleAll() {
  checkedUrls.value = allChecked.value
    ? new Set()
    : new Set(candidateRows.value.map((row) => row.base_url));
}

function submit() {
  if (!canConfirm.value) return;
  emit("confirm", {
    scope: scopeChoice.value,
    channelIds:
      scopeChoice.value === "selected" ? selectedChannelIds.value : [],
  });
}
</script>

<template>
  <Modal
    :open="open"
    title="同步主站"
    :subtitle="`选择本次同步的渠道范围：${siteLabel || '当前主站'}`"
    @close="emit('close')"
  >
    <div class="space-y-2">
      <label
        v-for="option in scopeOptions"
        :key="option.value"
        class="flex cursor-pointer items-start gap-2.5 rounded-[var(--radius-md)] border px-3 py-2.5 transition-colors duration-[var(--motion-fast)]"
        :class="
          scopeChoice === option.value
            ? 'border-accent bg-accent-soft'
            : 'border-line hover:bg-sunken-hover'
        "
      >
        <input
          v-model="scopeChoice"
          class="mt-0.5"
          type="radio"
          name="sync-scope"
          :value="option.value"
        />
        <span class="min-w-0">
          <span class="block text-[13px] font-semibold text-ink-strong">
            {{ option.label }}
          </span>
          <span class="mt-0.5 block text-[11.5px] leading-relaxed text-ink-muted">
            {{ option.hint }}
          </span>
        </span>
      </label>
    </div>

    <div
      v-if="scopeChoice === 'selected'"
      class="mt-4 rounded-[var(--radius-md)] border border-line"
    >
      <div
        class="flex items-center justify-between gap-2 border-b border-line-soft bg-panel-soft px-3 py-2"
      >
        <span class="text-[12px] font-semibold text-ink-muted">
          可同步渠道（平台识别为 NewAPI / sub2api）
        </span>
        <button
          type="button"
          class="text-[12px] font-medium text-accent hover:underline"
          @click="toggleAll"
        >
          {{ allChecked ? "清空" : "全选" }}
        </button>
      </div>
      <div class="max-h-64 overflow-y-auto px-3 py-2">
        <div v-if="candidatesLoading" class="flex items-center gap-2 py-6 text-sm text-ink-muted">
          <Spinner /> 正在读取可同步渠道
        </div>
        <p
          v-else-if="!candidateRows.length"
          class="py-6 text-center text-sm text-ink-muted"
        >
          没有识别为 NewAPI / sub2api 的渠道可勾选
        </p>
        <label
          v-for="candidate in candidateRows"
          :key="candidate.base_url"
          class="flex cursor-pointer items-start gap-2.5 rounded-[var(--radius-sm)] px-1 py-1.5 hover:bg-sunken-hover"
        >
          <input
            type="checkbox"
            class="mt-0.5"
            :checked="checkedUrls.has(candidate.base_url)"
            @change="toggleCandidate(candidate.base_url)"
            :aria-label="`选择 ${candidate.name}`"
          />
          <span class="min-w-0">
            <span class="block truncate text-[13px] font-medium text-ink-strong">
              {{ candidate.name }}
            </span>
            <span
              class="block truncate text-[11px] text-ink-soft"
              :title="candidate.base_url"
            >
              {{ candidate.base_url }} · {{ candidate.channel_count }} 个渠道
            </span>
          </span>
        </label>
      </div>
    </div>

    <div class="mt-5 flex items-center justify-end gap-2">
      <Button variant="secondary" @click="emit('close')">取消</Button>
      <Button :disabled="!canConfirm" data-testid="sync-scope-confirm" @click="submit">
        开始同步
      </Button>
    </div>
  </Modal>
</template>
