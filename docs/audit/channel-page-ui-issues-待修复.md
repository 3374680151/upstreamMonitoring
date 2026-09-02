# 模块四：主站渠道页 UI - 审计问题清单

> 审计时间：2026-09-02
> 审计范围：15 个文件、~4800 行（前端组件/composable/api 层）

---

## 问题总览

| 编号 | 严重度 | 状态 | 问题摘要 |
|------|--------|------|----------|
| UI-001 | 🔴 高 | ✅ 已修复（2026-09-03） | 浮层与弹窗分组排序规则不一致 |
| UI-002 | 🔴 高 | ✅ 已修复（2026-09-03） | `average` 计算函数多处重复 |
| UI-003 | 🔴 高 | ✅ 已修复（2026-09-03） | 分组汇总逻辑两处重复（popover vs dialog） |
| UI-004 | 🟡 中 | ✅ 已修复（2026-09-03） | 倍率显示格式不统一（`ratioLabel` vs `ratioXText`） |
| UI-005 | 🟡 中 | ✅ 已修复（2026-09-03） | `ChannelsPage` 本地 `ratioText()` 冗余且缺 `ratio_type=text` 处理 |
| UI-006 | 🟡 中 | ✅ 已确认：不改（2026-09-03） | 「上游不允许监控」规则 NewAPI 空分组文案不统一 |
| UI-007 | 🟡 中 | ✅ 已修复（2026-09-03） | 浮层 site 切换时数据闪屏（先清空再异步加载） |
| UI-008 | 🟢 低 | ✅ 已修复（2026-09-03） | `GroupSummaryBar` 无防御性空状态兜底 |
| UI-009 | 🟢 低 | ✅ 已确认：选项 A（2026-09-03） | 浮层打开后 trigger 位移不重新定位 |
| UI-010 | 🟢 低 | ✅ 已修复（2026-09-03） | `RatiosDialog` 双 watch 同依赖 immediate 执行顺序脆弱 |
| UI-011 | 🟢 低 | ✅ 已修复（2026-09-03） | `RatiosDialog` 的 `error` 状态跨 watch 覆盖风险 |
| UI-012 | 🟢 低 | ⏸ 延后 | `ChannelsPage.vue` 超 1600 行，ratio 逻辑可下沉 |

---

## 🔴 高风险问题

### UI-001：浮层与弹窗分组排序规则不一致

**位置**：
- `UpstreamGroupsPopover.vue:76-82`
- `RatiosDialog.vue:224-234`

**当前代码**：

```typescript
// UpstreamGroupsPopover.vue — 纯 ratio 值排序
.sort((a, b) => {
  const ratioDiff =
    (Number.isFinite(Number(a.item?.ratio)) ? Number(a.item.ratio) : Infinity) -
    (Number.isFinite(Number(b.item?.ratio)) ? Number(b.item.ratio) : Infinity);
  if (ratioDiff) return ratioDiff;
  return a.name.localeCompare(b.name, "zh-CN");
});

// RatiosDialog.vue — 先 groupPriority（gpt/claude 优先），再 ratio，再名称
.sort(([a, aItem], [b, bItem]) => {
  const aPriority = groupPriority(a);
  const bPriority = groupPriority(b);
  if (aPriority !== bPriority) return aPriority - bPriority;
  const aRatio = numericRatio(aItem);
  const bRatio = numericRatio(bItem);
  if (aRatio !== bRatio) return aRatio - bRatio;
  return a.localeCompare(b, "zh-CN");
});
```

**问题描述**：

同一站点的全量分组目录在 hover 浮层和点击查看弹窗里排列顺序不同。浮层按 ratio 纯值排序，弹窗优先把 gpt/claude 排到最前。违反「浮层与倍率弹窗一致」的设计要求。

**修复方案**：

统一排序逻辑到 `lib/perf.ts` 导出：

```typescript
// lib/perf.ts
export function compareGroupEntries(
  [aName, aItem]: [string, GroupItem],
  [bName, bItem]: [string, GroupItem],
): number {
  const aPriority = /gpt|claude|clade/i.test(aName) ? 0 : 1;
  const bPriority = /gpt|claude|clade/i.test(bName) ? 0 : 1;
  if (aPriority !== bPriority) return aPriority - bPriority;
  const aRatio = Number(aItem?.ratio);
  const bRatio = Number(bItem?.ratio);
  const aFinite = Number.isFinite(aRatio) ? aRatio : Infinity;
  const bFinite = Number.isFinite(bRatio) ? bRatio : Infinity;
  if (aFinite !== bFinite) return aFinite - bFinite;
  return aName.localeCompare(bName, "zh-CN");
}
```

两处调用方统一使用 `entries.sort(compareGroupEntries)`。

**涉及文件**：`lib/perf.ts`（新增导出）、`UpstreamGroupsPopover.vue`、`RatiosDialog.vue`

---

### UI-002：`average` 计算函数三处重复

**位置**：
- `UpstreamGroupsPopover.vue:95-102`（`average()`，闭包内嵌）
- `RatiosDialog.vue:68-75`（`averageNumbers()`）
- `lib/perf.ts`（缺失，应在此处）

**问题描述**：

三处逻辑完全相同：过滤 `NaN` → 求平均 → 无有效值返回 `null`。

**修复方案**：

下沉到 `lib/perf.ts` 统一导出：

```typescript
// lib/perf.ts
export function averageNumbers(values: Array<number | undefined>): number | null {
  const valid = values
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value));
  return valid.length
    ? valid.reduce((sum, value) => sum + value, 0) / valid.length
    : null;
}
```

删除 `UpstreamGroupsPopover.vue` 的 `average()` 和 `RatiosDialog.vue` 的 `averageNumbers()`，统一 import。

**涉及文件**：`lib/perf.ts`（新增导出）、`UpstreamGroupsPopover.vue`、`RatiosDialog.vue`

---

### UI-003：分组汇总逻辑两处重复

**位置**：
- `UpstreamGroupsPopover.vue:94-142`（`groupSummary()`，NewAPI + sub2api 合一）
- `RatiosDialog.vue:77-122`（`summarizeNewApiGroup()` + `summarizeLegacyGroup()`，拆成两个函数）

**问题描述**：

两处的 sub2api 分支逻辑完全一致（`availability_7d`、`latency_ms` / `ping_latency_ms`、`avgTps: null`），NewAPI 分支也高度相似（pricing 过滤 + perf map 查找 + 采样计数）。

**修复方案**：

抽出共享函数到 `lib/perf.ts`：

```typescript
// lib/perf.ts — 新增
export interface GroupSummary {
  modelCount: number;
  monitoredCount: number;
  successRate: number | null;
  avgLatencyMs: number | null;
  avgTps: number | null;
  sampleCount: number | null;
}

export function summarizeNewApiGroup(
  groupName: string,
  pricing: PricingResponse | null,
  perfMap: Map<string, PerfSummaryModel>,
): GroupSummary { /* ... */ }

export function summarizeSub2ApiGroup(
  groupName: string,
  models: Record<string, ModelHealth[]> | null,
): GroupSummary { /* ... */ }
```

两处组件统一调用共享函数。

**涉及文件**：`lib/perf.ts`（新增导出 + 类型）、`UpstreamGroupsPopover.vue`、`RatiosDialog.vue`

---

## 🟡 中风险问题

### UI-004：倍率显示格式不统一

**位置**：
- `lib/format.ts:147-154`（`ratioLabel`：`1.500000x`）
- `lib/format.ts:157-162`（`ratioXText`：`×1.5`）
- `RatiosDialog.vue:406` 用 `ratioLabel`
- `UpstreamGroupsPopover.vue:328` 用 `ratioXText`
- `ChannelsPage.vue:1425` 用 `ratioXText`

**问题描述**：

弹窗使用 `ratioLabel`（6 位小数 + `x` 后缀），浮层和 badge 使用 `ratioXText`（`×` 前缀 + 原始精度）。同一站点在不同视图的倍率显示精度和格式不同。设计要求「浮层与倍率弹窗一致」。

**修复方案**：

统一 RatiosDialog 的倍率列为 `ratioXText`（与浮层一致），保留 `ratioLabel` 供需要高精度的场景（如变化记录详情）使用。

**涉及文件**：`RatiosDialog.vue`

---

### UI-005：`ChannelsPage` 本地 `ratioText()` 冗余

**位置**：`ChannelsPage.vue:66-71`

**当前代码**：

```typescript
function ratioText(item?: GroupItem): string {
  if (!item) return "—";
  const ratio = item.ratio;
  if (ratio === undefined || ratio === null || ratio === "") return "—";
  return typeof ratio === "number" ? `×${ratio}` : String(ratio);
}
```

**问题描述**：

与 `lib/format.ts` 的 `ratioXText()` 功能高度重叠，但缺少 `ratio_type === "text"` 处理。只在 `ChannelsPage.vue:1303` 的侧栏分组卡片使用。

**修复方案**：

删除本地 `ratioText()`，改用 `ratioXText`（已 import）。侧栏 badge 从：

```vue
<Badge>倍率 {{ ratioText(groups[name]) }}</Badge>
```

改为：

```vue
<Badge>倍率 {{ ratioXText(groups[name]) }}</Badge>
```

**涉及文件**：`ChannelsPage.vue`

---

### UI-006：「上游不允许监控」规则 NewAPI 空分组文案不统一

**位置**：
- `UpstreamGroupsPopover.vue:332-338`
- `RatiosDialog.vue:284-288`
- `ModelCell.vue:82-84` vs `103-105`

**问题描述**：

NewAPI 空分组在三个视图里展示三种不同文案：

| 视图 | NewAPI 空分组 | sub2api 空分组 |
|------|--------------|---------------|
| 浮层 | `暂无模型汇总`（neutral） | `上游不允许监控`（warning） |
| 弹窗 GroupSummaryBar | `模型 0/0 · 暂无成功率` | `上游不允许监控`（warning） |
| 弹窗 ModelCell | `上游未返回该分组的模型数据` | `上游不允许监控`（warning） |

**待确认**：

NewAPI 空分组与 sub2api 空分组是否需要区分语义？
- NewAPI 空分组 = 该分组无模型配置（正常状态）
- sub2api 空分组 = 上游未配置监控器（限制状态）

若需统一，可在 `RatiosDialog` 的 `upstreamBlocked` 条件中对 NewAPI 空分组也设 `upstreamBlocked=true`，或新增 `emptyGroup` 标识。

**涉及文件**：`UpstreamGroupsPopover.vue`、`RatiosDialog.vue`、`ModelCell.vue`

---

### UI-007：浮层 site 切换时数据闪屏

**位置**：`UpstreamGroupsPopover.vue:51,208-215`

**当前代码**：

```typescript
let loadedSiteId: number | null = null;  // 非响应式

async function loadDetails(): Promise<void> {
  if (!site || loadedSiteId === site.id || detailLoading.value) return;
  pricing.value = null;      // ← 立即清空
  perfModels.value = [];
  models.value = null;
  // ...异步加载
}
```

**问题描述**：

切换到不同站点时，旧数据被立即清空（`pricing.value = null`），新数据异步加载。用户会看到浮层从「有数据」闪回「正在读取…」再恢复。如果目标站点数据已缓存（后端 SWR），闪屏期间虽短但仍可感知。

**修复方案**：

先加载新数据，完成后原子替换：

```typescript
async function loadDetails(): Promise<void> {
  const site = props.site;
  if (!site || loadedSiteId === site.id || detailLoading.value) return;
  detailLoading.value = true;
  try {
    if (site.platform === "newapi") {
      const [pricingResult, perfResult] = await Promise.allSettled([...]);
      if (props.site?.id !== site.id) return;
      // 全部完成后一次性替换
      pricing.value = pricingResult.status === "fulfilled" ? pricingResult.value : null;
      perfModels.value = perfResult.status === "fulfilled" ? perfResult.value.data?.models || [] : [];
      loadedSiteId = site.id;
    } else {
      const resp = await api.siteModels(site.id);
      if (props.site?.id !== site.id) return;
      models.value = resp.models_by_group || {};
      loadedSiteId = site.id;
    }
  } finally {
    detailLoading.value = false;
  }
}
```

**涉及文件**：`UpstreamGroupsPopover.vue`

---

## 🟢 低风险问题

### UI-008：`GroupSummaryBar` 无防御性空状态兜底

**位置**：`GroupSummaryBar.vue:27-29`

**当前代码**：

```vue
<Badge :tone="tone">
  {{ summary.successRate == null ? "暂无成功率" : `成功率 ${formatRate(summary.successRate)}` }}
</Badge>
```

**问题描述**：

如果上游拦截逻辑变化导致空分组也渲染 `GroupSummaryBar`，会显示「暂无成功率 · 延迟 - · TPS - · 样本 - · 模型 0/0」——一行全是无效数据。

**修复方案**：

在组件内加一层守卫：

```vue
<template v-if="summary.modelCount === 0">
  <span class="text-[11px] font-semibold text-warning-fg">暂无模型数据</span>
</template>
<template v-else>
  <!-- 原有内容 -->
</template>
```

**涉及文件**：`GroupSummaryBar.vue`

---

### UI-009：浮层打开后 trigger 位移不重新定位

**位置**：`UpstreamGroupsPopover.vue:152-154`

**问题描述**：

`positionPopover()` 只在 `scheduleOpen` 时调用一次。浮层打开后 trigger 因表格重排/列宽变化而位移时，浮层位置不更新（只有 scroll/resize 会关闭浮层）。在高密度表格场景下可能错位。

**修复方案**：

选项 A（简单）：保持现状，依赖 scroll/resize 关闭浮层（当前行为）。
选项 B（完善）：监听 `ResizeObserver` on triggerEl，位移超过阈值时重新定位或关闭。

**涉及文件**：`UpstreamGroupsPopover.vue`

---

### UI-010：`RatiosDialog` 双 watch 同依赖 immediate 执行顺序脆弱

**位置**：`RatiosDialog.vue:125-193`（Watch 1）和 `197-208`（Watch 2）

**问题描述**：

两个 watch 监听同一组依赖 `[open, site?.id]` 且都有 `immediate: true`。Watch 1 负责数据加载（会 `error.value = ""`），Watch 2 负责状态重置（不重置 `error`）。执行顺序依赖 Vue 的 watch 注册顺序——重构调整顺序可能导致 `error` 残留旧值。

**修复方案**：

合并为一个 watch，或在 Watch 2 中显式重置 `error`。

**涉及文件**：`RatiosDialog.vue`

---

### UI-011：`RatiosDialog` 的 `error` 状态跨 watch 覆盖风险

**位置**：`RatiosDialog.vue:140,161-170,189-190`

**问题描述**：

Watch 1 内 `error.value = ""`（line 140），异步回调内 `error.value = issues.join("；")`（line 170）。如果 Watch 2（line 197）在 Watch 1 的异步回调完成前执行，`error` 不会被重置（Watch 2 不管 error）。虽然目前靠执行顺序保证正确，但脆弱。

**修复方案**：同 UI-010，合并 watch 或显式管理 error 生命周期。

**涉及文件**：`RatiosDialog.vue`

---

### UI-012：`ChannelsPage.vue` 超 1600 行

**位置**：`ChannelsPage.vue`（1630 行）

**问题描述**：

远超 500 行红线。ratio 相关逻辑（`ratioText`、`staleMatchPrefix`、`bindingFailure`、`ratioRefreshStatusText` 等）占约 150 行，可下沉到 composable 或 service 层。

**修复方案**：

拆出 `useChannelRatio` composable，收纳：
- `ratioText` → 改用 `ratioXText` 后删除
- `staleMatchPrefix`
- `bindingFailure`
- `bindingStatusLabel`
- `isStaleMatchStatus`
- `isAuthExpiredBinding`
- ratio 刷新相关状态与函数

**涉及文件**：`ChannelsPage.vue`（拆分）、新建 `composables/useChannelRatio.ts`

---

## 附录：修复优先级建议

### 批次一：核心冗余消除（影响面最大）

| 编号 | 改动 | 工作量 |
|------|------|--------|
| UI-002 | `averageNumbers` 下沉到 `lib/perf.ts` | 小 |
| UI-003 | 分组汇总逻辑下沉到 `lib/perf.ts` | 中 |
| UI-001 | 排序逻辑统一到 `lib/perf.ts` | 小 |

### 批次二：格式统一

| 编号 | 改动 | 工作量 |
|------|------|--------|
| UI-004 | RatiosDialog 倍率列改用 `ratioXText` | 小 |
| UI-005 | 删除 ChannelsPage 本地 `ratioText()` | 小 |

### 批次三：行为修复

| 编号 | 改动 | 工作量 |
|------|------|--------|
| UI-006 | 「上游不允许监控」规则统一 | 中（需确认语义） |
| UI-007 | 浮层 site 切换原子替换 | 小 |

### 批次四：防御性 & 结构优化

| 编号 | 改动 | 工作量 |
|------|------|--------|
| UI-008 | GroupSummaryBar 空状态守卫 | 小 |
| UI-009 | 浮层定位健壮性 | 小（可延后） |
| UI-010/011 | RatiosDialog watch 合并 | 小 |
| UI-012 | ChannelsPage 拆分 composable | 大（可延后） |

---

## 涉及文件索引

### 前端（需修改）

| 文件 | 问题编号 |
|------|----------|
| `apps/web/src/lib/perf.ts` | UI-002, UI-003, UI-001（新增导出） |
| `apps/web/src/components/UpstreamGroupsPopover.vue` | UI-001, UI-002, UI-003, UI-007, UI-009 |
| `apps/web/src/components/RatiosDialog.vue` | UI-001, UI-002, UI-003, UI-004, UI-010, UI-011 |
| `apps/web/src/components/GroupSummaryBar.vue` | UI-008 |
| `apps/web/src/components/ModelCell.vue` | UI-006（待确认） |
| `apps/web/src/pages/ChannelsPage.vue` | UI-005, UI-012 |

### 前端（无需修改）

| 文件 | 说明 |
|------|------|
| `apps/web/src/lib/format.ts` | `ratioXText` / `ratioLabel` 保留，不改 |
| `apps/web/src/components/ui/Modal.vue` | 无问题 |
| `apps/web/src/composables/useAppActions.ts` | 无问题 |
| `apps/web/src/lib/api/adminSites.ts` | 无问题 |
| `apps/web/src/lib/api/monitoring.ts` | 无问题 |

---

## 处理记录（2026-09-03，分支 `audit-cleanup-fixes`）

复核勘误：
- **UI-002** 实际重复实现是 **2 处**（浮层闭包 `average` + 弹窗 `averageNumbers`），
  原文「三处」把建议下沉的位置 `lib/perf.ts` 也计入了。
- **UI-004** 示例值有误：`ratioLabel` 的 `Intl.NumberFormat` 是
  `minimumFractionDigits: 2, maximumFractionDigits: 6`，1.5 实际渲染
  `1.50x` 而非 `1.500000x`。不一致的结论本身成立。

处理结果：
- **UI-001/002/003**：`compareGroupEntries` / `averageNumbers` /
  `summarizeNewApiGroup` / `summarizeSub2ApiGroup`（含 `GroupSummary` 类型）
  下沉 `lib/perf.ts`，浮层与弹窗统一调用。浮层排序随之变为
  「gpt/claude 优先 → 倍率 → 名称」（与弹窗一致，属本条修复目的）。
- **UI-004**：`RatiosDialog` 倍率列改用 `ratioXText`；`ratioLabel` 保留给
  需要固定位精度的场景（如 ModelCell 模型行、变化记录）。
- **UI-005**：删除 `ChannelsPage.ratioText`，侧栏 badge 改用 `ratioXText`
  （顺带补上 `ratio_type=text` 处理）。
- **UI-006**：确认后不改。三处文案对应**不同加载状态**而非同一状态的不一致：
  浮层「暂无模型汇总」出现在 pricing 未加载/失败时；弹窗 ModelCell
  「上游未返回该分组的模型数据」出现在 pricing 已加载但分组为空时；
  弹窗 GroupSummaryBar「模型 0/0 · 暂无成功率」是正常的数据驱动展示。
  sub2api 的「上游不允许监控」三处语义已一致；NewAPI 空分组=正常态、
  sub2api 空分组=限制态的语义区分应当保留。
- **UI-007**：采用比原文方案更严格的实现——`dataSiteId` 门控渲染，
  加载期间保留旧数据但不渲染，完成后原子切换；既消除闪屏，也不会在
  加载期间把旧站点数据显示在新站点名下（原方案的缺陷）。
- **UI-008**：加了空态守卫，但文案用中性色「暂无模型数据」而非原文建议的
  `text-warning-fg`——NewAPI 空分组是正常状态（见 UI-006 语义），
  warning 语义留给「上游不允许监控」。
- **UI-009**：采纳选项 A（保持现状，依赖 scroll/resize 关闭浮层）。
- **UI-010/011**：Watch 2 显式重置 `error`，不再依赖注册顺序。
- **UI-012**：延后。本次顺带删除了 `ratioText`（约 6 行），完整拆分
  `useChannelRatio` composable 留待后续批次（触及时一并做）。
