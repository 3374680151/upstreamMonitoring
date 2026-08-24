<script setup lang="ts">
import { computed } from "vue";
import { Pencil, Power, PowerOff, RefreshCw } from "lucide-vue-next";
import Badge from "@/components/Badge.vue";
import { normalizedChannelStatus } from "@/lib/sub2apiChannel";
import type { Channel } from "@/lib/types";

type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "brand";

interface Props {
  channels: Channel[];
  busyChannelIds: Set<number>;
  canEdit: boolean;
  canToggle: boolean;
}
const props = defineProps<Props>();
const emit = defineEmits<{
  edit: [channel: Channel];
  toggle: [channel: Channel];
  refresh: [channel: Channel];
}>();

const iconButtonClass =
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-sm)] border border-line bg-panel text-ink-muted transition hover:border-line-strong hover:bg-sunken-hover hover:text-ink-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] disabled:pointer-events-none disabled:opacity-50";

function statusMeta(channel: Channel): { label: string; tone: Tone } {
  const status = normalizedChannelStatus(channel);
  if (status === "active") return { label: "运行中", tone: "success" };
  if (status === "disabled") return { label: "已停用", tone: "warning" };
  return { label: "异常", tone: "danger" };
}

function multiplier(value?: number | null): string {
  return value === undefined || value === null ? "--" : `x${value}`;
}

function modelSummary(channel: Channel): string {
  const pricing = channel.model_pricing || [];
  const modelCount = new Set(
    pricing.flatMap((item) => item.models || []).filter(Boolean),
  ).size;
  const platforms = [
    ...new Set(pricing.map((item) => item.platform).filter(Boolean)),
  ];
  if (!pricing.length) return "未配置";
  return `${modelCount} 模型 · ${platforms.join(" / ") || "未标注平台"}`;
}

function billingSummary(channel: Channel): string {
  const modes = [
    ...new Set(
      (channel.model_pricing || [])
        .map((item) => item.billing_mode)
        .filter(Boolean),
    ),
  ];
  const source = channel.billing_model_source || "channel";
  return `${source} · ${modes.join(" / ") || "未配置"}`;
}

const rows = computed(() =>
  props.channels.map((channel) => ({
    channel,
    meta: statusMeta(channel),
    busy: props.busyChannelIds.has(channel.id),
    active: normalizedChannelStatus(channel) === "active",
    groups: channel.groups || [],
  })),
);
</script>

<template>
  <div class="priceai-scrollbar max-h-[calc(100vh-18rem)] overflow-auto rounded-[var(--radius-sm)]">
    <table class="w-full min-w-[980px] table-fixed text-left text-sm">
      <colgroup>
        <col class="w-[185px]" />
        <col class="w-[85px]" />
        <col class="w-[135px]" />
        <col class="w-[150px]" />
        <col class="w-[160px]" />
        <col class="w-[140px]" />
        <col class="w-[125px]" />
      </colgroup>
      <thead class="sticky top-0 z-10 bg-panel">
        <tr class="border-b border-line-soft text-[12.5px] font-semibold text-ink-muted">
          <th class="pb-2">渠道</th>
          <th class="pb-2">状态</th>
          <th class="pb-2">分组</th>
          <th class="pb-2">分组倍率</th>
          <th class="pb-2">模型定价</th>
          <th class="pb-2">计费</th>
          <th class="pb-2 text-right">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.channel.id"
          class="border-b border-line-soft last:border-0 hover:bg-sunken-hover"
        >
          <td class="max-w-0 py-3 pr-3">
            <div class="truncate font-bold text-ink-strong">
              {{ row.channel.name || `#${row.channel.id}` }}
            </div>
            <div class="truncate text-[11px] text-ink-soft">
              #{{ row.channel.id }}<span v-if="row.channel.description"> · {{ row.channel.description }}</span>
            </div>
          </td>
          <td class="py-3 pr-3">
            <Badge :tone="row.meta.tone" dot>
              {{ row.meta.label }}
            </Badge>
          </td>
          <td class="max-w-0 py-3 pr-3">
            <div class="flex flex-wrap gap-1">
              <Badge
                v-for="group in row.groups"
                :key="group.id"
                tone="info"
              >
                {{ group.name }}
              </Badge>
              <span v-if="!row.groups.length" class="text-[12.5px] text-ink-soft">--</span>
            </div>
          </td>
          <td class="max-w-0 py-3 pr-3">
            <div class="flex flex-wrap gap-1 tabular-nums">
              <Badge
                v-for="group in row.groups"
                :key="group.id"
                tone="success"
              >
                {{ group.name }} {{ multiplier(group.rate_multiplier) }}
              </Badge>
              <span v-if="!row.groups.length" class="text-[12.5px] text-ink-soft">--</span>
            </div>
          </td>
          <td class="max-w-0 py-3 pr-3">
            <div class="truncate text-[12.5px] text-ink" :title="modelSummary(row.channel)">
              {{ modelSummary(row.channel) }}
            </div>
            <div class="mt-0.5 text-[11px] tabular-nums text-ink-soft">
              {{ (row.channel.model_pricing || []).length }} 条定价规则
            </div>
          </td>
          <td class="max-w-0 py-3 pr-3">
            <div class="truncate text-[12.5px] text-ink" :title="billingSummary(row.channel)">
              {{ billingSummary(row.channel) }}
            </div>
          </td>
          <td class="py-3">
            <div class="flex min-h-8 items-center justify-end gap-1.5">
              <button
                type="button"
                :class="iconButtonClass"
                title="刷新主站数据"
                :aria-label="`刷新渠道 ${row.channel.name || row.channel.id}`"
                :disabled="row.busy"
                @click="emit('refresh', row.channel)"
              >
                <RefreshCw :size="15" :class="row.busy ? 'animate-spin' : ''" />
              </button>
              <button
                v-if="canToggle"
                type="button"
                :class="iconButtonClass"
                :title="row.active ? '停用渠道' : '启用渠道'"
                :aria-label="`${row.active ? '停用' : '启用'}渠道 ${row.channel.name || row.channel.id}`"
                :disabled="row.busy"
                @click="emit('toggle', row.channel)"
              >
                <PowerOff v-if="row.active" :size="15" />
                <Power v-else :size="15" />
              </button>
              <button
                v-if="canEdit"
                type="button"
                :class="iconButtonClass"
                title="编辑渠道配置"
                :aria-label="`编辑渠道 ${row.channel.name || row.channel.id}`"
                :disabled="row.busy"
                @click="emit('edit', row.channel)"
              >
                <Pencil :size="15" />
              </button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
