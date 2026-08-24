<script setup lang="ts">
import {
  changeDisplayMessage,
  changeTone,
  changeTypeLabel,
  fmtTimeParts,
  siteNameById,
} from "@/lib/format";
import type { Change, Site } from "@/lib/types";
import Badge from "@/components/Badge.vue";
import ChangeValue from "@/components/ChangeValue.vue";

interface Props {
  changes: Change[];
  sites: Site[];
  showSite?: boolean;
}
withDefaults(defineProps<Props>(), {
  showSite: true,
});

function timeParts(value?: string | null): [string, string] {
  return fmtTimeParts(value);
}
</script>

<template>
  <div v-if="!changes.length" class="flex flex-col items-center gap-1 py-8 text-center">
    <div class="font-serif text-[14px] font-semibold text-ink-strong">暂无变化</div>
    <p class="text-[12px] text-ink-muted">系统每次检测后会把变化的分组倍率写到这里。</p>
  </div>
  <div v-else class="priceai-scrollbar min-w-0 overflow-x-auto pb-1">
    <table class="w-full min-w-max table-auto text-left text-[13px]">
      <thead class="sticky top-0 z-10 bg-panel">
        <tr class="border-b border-line text-[11.5px] font-semibold tracking-[0.04em] text-ink-soft uppercase">
          <th class="whitespace-nowrap pb-2.5 pr-3">时间</th>
          <th v-if="showSite" class="whitespace-nowrap pb-2.5 pr-3">渠道</th>
          <th class="whitespace-nowrap pb-2.5 pr-3">类型</th>
          <th class="whitespace-nowrap pb-2.5 pr-3">分组</th>
          <th class="whitespace-nowrap pb-2.5">变化</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="change in changes"
          :key="change.id"
          class="border-b border-line-soft transition-colors duration-[var(--motion-fast)] last:border-0 hover:bg-sunken-hover"
        >
          <td class="py-3 pr-3 align-top text-[11.5px] text-ink-muted">
            <span class="whitespace-nowrap tabular leading-[1.35]">
              <span class="block">{{ timeParts(change.created_at)[0] }}</span>
              <span v-if="timeParts(change.created_at)[1]" class="block text-ink-soft">
                {{ timeParts(change.created_at)[1] }}
              </span>
            </span>
          </td>
          <td v-if="showSite" class="py-3 pr-3 align-top font-medium text-ink-strong">
            {{ siteNameById(sites, change.site_id) }}
          </td>
          <td class="py-3 pr-3 align-top">
            <Badge :tone="changeTone(change)">
              {{ changeTypeLabel(change.change_type) }}
            </Badge>
          </td>
          <td class="py-3 pr-3 align-top font-medium text-ink">
            {{ change.group_name || "-" }}
          </td>
          <td class="py-3 align-top">
            <ChangeValue :message="changeDisplayMessage(change)" />
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
