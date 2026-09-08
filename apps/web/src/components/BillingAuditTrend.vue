<script setup lang="ts">
/**
 * 计费核对趋势带 — 每个桶 = 一轮核对（默认 5 分钟）。
 * 堆叠柱：绿 ok / 红 mismatch / 灰 unknown；红桶顶部加 ▲ 形状标记，
 * 不只靠颜色区分（无障碍）。自绘 div 柱，不引图表库。
 */
import { computed } from "vue";
import type { BillingTimeBucket } from "@/lib/types";
import { usdPrecise } from "@/lib/format";

interface Props {
  buckets: BillingTimeBucket[];
  bucketMinutes: number;
  loading?: boolean;
}
const props = defineProps<Props>();
const emit = defineEmits<{ select: [bucket: BillingTimeBucket] }>();

const BAR_AREA_PX = 64;

/** 只返回非空桶，但趋势带要补齐中间的空桶（零高度）保证时间轴连续 */
const timeline = computed(() => {
  const list = props.buckets;
  if (!list.length) return [];
  const step = props.bucketMinutes * 60;
  const span = Math.floor(
    (new Date(list[list.length - 1].bucket_start_at).getTime() -
      new Date(list[0].bucket_start_at).getTime()) / 1000,
  );
  // 空桶补齐上限：超过 96 根时按密度抽稀（只渲染有数据的桶）
  if (span / step > 96) return list;
  const byStart = new Map(
    list.map((b) => [Math.floor(new Date(b.bucket_start_at).getTime() / 1000), b]),
  );
  const out: (BillingTimeBucket | null)[] = [];
  for (let ts = Math.floor(new Date(list[0].bucket_start_at).getTime() / 1000); ; ts += step) {
    out.push(byStart.get(ts) ?? null);
    if (ts >= Math.floor(new Date(list[list.length - 1].bucket_start_at).getTime() / 1000)) break;
  }
  return out;
});

const maxTotal = computed(() =>
  Math.max(1, ...props.buckets.map((b) => b.total_count)),
);

const totals = computed(() => ({
  ok: props.buckets.reduce((acc, b) => acc + b.ok_count, 0),
  mismatch: props.buckets.reduce((acc, b) => acc + b.mismatch_count, 0),
  unknown: props.buckets.reduce((acc, b) => acc + b.unknown_count, 0),
}));

function bucketLabel(bucket: BillingTimeBucket): string {
  const d = new Date(bucket.bucket_start_at);
  const pad = (p: number) => String(p).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function bucketTitle(item: BillingTimeBucket | null): string {
  if (!item) return "该轮无核对记录";
  return [
    bucketLabel(item),
    `核对 ${item.total_count} · 红 ${item.mismatch_count} · 灰 ${item.unknown_count}`,
    `毛利 ${usdPrecise(item.margin_usd)}`,
    item.mismatch_count ? "点击筛选该轮明细" : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function segmentHeight(count: number): string {
  return `${Math.max(count > 0 ? 6 : 0, Math.round((count / maxTotal.value) * BAR_AREA_PX))}px`;
}
</script>

<template>
  <div>
    <div v-if="!buckets.length" class="py-6 text-center text-[12.5px] text-ink-muted">
      当前时间范围内还没有核对记录
    </div>
    <div v-else>
      <div class="flex h-[76px] items-end gap-[2px]" role="img" :aria-label="`趋势带：${totals.ok} 正常、${totals.mismatch} 异常、${totals.unknown} 无法核对`">
        <div
          v-for="(item, index) in timeline"
          :key="index"
          class="group relative flex min-w-[3px] flex-1 cursor-pointer flex-col items-center justify-end"
          :class="item && item.total_count ? '' : 'pointer-events-none'"
          @click="item && emit('select', item)"
        >
          <span
            v-if="item?.mismatch_count"
            class="mb-0.5 text-[9px] leading-none text-danger-fg"
            aria-hidden="true"
          >▲</span>
          <div
            class="w-full rounded-t-[1px] bg-danger-fg/80"
            :style="{ height: item ? segmentHeight(item.mismatch_count) : '0px' }"
          />
          <div
            class="w-full bg-warning-fg/60"
            :style="{ height: item ? segmentHeight(item.unknown_count) : '0px' }"
          />
          <div
            class="w-full rounded-b-[1px] bg-accent/70"
            :style="{ height: item ? segmentHeight(item.ok_count) : '0px' }"
          />
          <span
            class="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1 hidden -translate-x-1/2 whitespace-nowrap rounded-[var(--radius-sm)] border border-line bg-panel px-2 py-1 text-[11px] text-ink shadow-[var(--shadow-pop)] group-hover:block"
          >
            {{ bucketTitle(item) }}
          </span>
        </div>
      </div>
      <div class="mt-2 flex items-center justify-between text-[11.5px] text-ink-muted">
        <span class="tabular">{{ buckets.length ? bucketLabel(buckets[0]) : "" }}</span>
        <span class="flex items-center gap-3">
          <span class="flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-accent" aria-hidden="true" />正常 {{ totals.ok }}</span>
          <span class="flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-danger-fg" aria-hidden="true" />异常 {{ totals.mismatch }}</span>
          <span class="flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-warning-fg" aria-hidden="true" />无法核对 {{ totals.unknown }}</span>
        </span>
        <span class="tabular">{{ buckets.length ? bucketLabel(buckets[buckets.length - 1]) : "" }}</span>
      </div>
    </div>
    <p v-if="loading" class="mt-1 text-[11.5px] text-ink-muted">统计中…</p>
  </div>
</template>
