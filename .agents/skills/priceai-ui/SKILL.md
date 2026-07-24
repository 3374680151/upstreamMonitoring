---
name: priceai-ui
description: Apply PriceAI (priceai.cc) visual language to Upstream console UI — cool gray-green surfaces, emerald brand accents, dense KPI cards, light borders, dual theme.
argument-hint: ["[component|page|review]"]
---

# /priceai-ui — PriceAI 风格 UI Skill

参考站点：

- 列表/中转生态：`https://priceai.cc`
- 详情页范本：`https://priceai.cc/api-transit/ai-rtoc-cc?back=family%3Dgpt%26sort%3Drate`

当用户要求实现/修改 Upstream 前端页面、组件、主题时，**必须**按本 skill 输出。

---

## 何时使用

- 新建页面、组件、布局
- 改色、改间距、改表格/卡片样式
- Code review 前端是否「像 PriceAI」
- 补 dark mode

---

## 设计 DNA（先读这段）

PriceAI 不是「赛博霓虹大屏」，也不是 Ant Design 默认后台。它是：

| 维度 | 特征 |
|------|------|
| 气质 | 冷静比价雷达 / 数据台账 |
| 色调 | 冷灰绿炭色文字 + 浅灰页面底 + **翠绿**品牌点 |
| 密度 | 高信息密度，KPI + 可折叠分组表 + 监测样本 |
| 形状 | 大圆角胶囊状态、中等卡片圆角、细边框 |
| 动效 | 轻微 `transition`，无花哨动画 |
| 字体 | 中文无衬线（PingFang SC / Microsoft YaHei）；数字 `tabular-nums` |
| 主题 | 默认浅色；`html[data-theme=dark]` 深青灰 |

---

## 设计 Token（权威）

实现时优先引用 `apps/web/src/styles/tokens.css`。下面是从 priceai.cc 静态 CSS 提炼的语义色。

### Light（默认）

| Token | 值 | 用途 |
|-------|-----|------|
| `--color-page` | `#f9f9f9` | 页面背景 |
| `--color-panel` | `#ffffff` | 卡片/面板 |
| `--color-panel-soft` | `#fbfcfc` | 次级面板 |
| `--color-surface` | `#f2f4f4` | 输入底、条带 |
| `--color-surface-hover` | `#edf0f1` | hover |
| `--color-surface-selected` | `#dde4e5` | 选中行 |
| `--color-text-primary` | `#202829` | 标题/强调字 |
| `--color-text-body` | `#2d3435` | 正文 |
| `--color-text-muted` | `#5a6061` | 次要说明 |
| `--color-text-soft` | `#6f7778` | 更弱 |
| `--color-text-placeholder` | `#9aa2a3` | placeholder |
| `--color-text-on-primary` | `#f8f8f8` | 深底上的字 |
| `--color-border` | `#dfe4e5` | 默认边框 |
| `--color-border-subtle` | `#edf0f1` | 分割线 |
| `--color-primary` | `#2d3435` | 主按钮底 |
| `--color-primary-strong` | `#202829` | 更深主色 |
| `--color-brand` | `#45bf78` | 品牌/可用/成功点 |
| `--color-success-bg` | `#e8f3ec` | 成功底 |
| `--color-success-text` | `#2f7a4b` | 成功字 |
| `--color-warning-bg` | `#fff7e8` | 警告底 |
| `--color-warning-text` | `#7a541b` | 警告字 |
| `--color-info-bg` | `#eef3f8` | 信息底 |
| `--color-info-text` | `#47657a` | 信息字 |
| `--color-danger-bg` | `#fbe9e7` | 危险底 |
| `--color-danger-text` | `#9b3328` | 危险字 |
| `--color-overlay` | `#20282959` | 遮罩 |

阴影：

- surface: `0 20px 55px #2d34350b`
- control: `0 10px 30px #2d34350f`
- floating: `0 30px 80px #2d34352e`

圆角：`md .375rem` / `lg .5rem` / `xl .75rem` / `2xl 1rem`；胶囊 `rounded-full`。

### Dark

| Token | 值 |
|-------|-----|
| page | `#111718` |
| panel | `#182122` |
| surface | `#1f292a` |
| text-primary | `#d7e1de` |
| text-body | `#c8d2cf` |
| text-muted | `#9eaaa7` |
| border | `#334142` |
| brand | `#65cc8c` |

---

## 布局模式

### 1) 应用壳

```
┌─────────────────────────────────────────────┐
│ Logo  导航(中转/模型/样本...)  主题  账户   │  sticky top bar, 白/panel 底 + 底边框
├─────────────────────────────────────────────┤
│ ← 返回列表                                  │
│ [Avatar] 渠道名  徽章(可用/New API/已核验)   │  头区
│ 说明文案（muted，1–3 行）                    │
│ ┌────┐ ┌────┐ ┌────┐ ┌────┐                │  KPI 四宫格/横滑
│ │倍率│ │可用│ │样本│ │检查│                │
│ └────┘ └────┘ └────┘ └────┘                │
│ 优惠/告警条（可选）                          │
│ 折叠区块：价格表 / 分组表 / 趋势             │
│ 表格：监测样本                               │
└─────────────────────────────────────────────┘
```

### 2) KPI 卡

- 白底 panel + `border border-[var(--color-border)]` + `rounded-2xl`
- 上：小标签 `text-xs text-muted`
- 下：大数字 `text-2xl/3xl font-extrabold tabular-nums text-primary`
- 辅行：`text-xs text-soft`（如「2 个分组」「样本 972」）
- 可选左侧 4px brand 竖条（`w-[4px] bg-[var(--color-brand)] rounded-full`）

### 3) 状态胶囊 Badge

| 语义 | 样式 |
|------|------|
| 可用/成功 | bg success-bg + text success-text；或实心 brand 点 |
| 已核验 | surface + border + muted/primary 字 |
| 警告 | warning-bg + warning-text |
| 危险/不可用 | danger-bg + danger-text |
| 信息 | info-bg + info-text |

形态：`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[10px]|text-xs font-semibold`

### 4) 数据表

- 表头：`text-xs font-semibold text-muted`，底边框 subtle
- 行：hover `bg-surface-hover`；数字列右对齐 + tabular-nums
- 分组行可展开；子行缩进；监测来源用虚线边框小标签

### 5) 顶栏导航

- 左：Logo 字标（font-extrabold）+ 产品名
- 中：文本链，默认 muted，hover → primary
- 右：明暗切换、账户
- 高度紧凑；`border-b border-[var(--color-border)]`；背景 panel / page-translucent + blur 可选

---

## Tailwind 写法约定

优先 CSS 变量，而不是散落的任意 hex（token 文件里已经映射）：

```tsx
// ✅
<div className="bg-[var(--color-panel)] border border-[var(--color-border)] rounded-2xl p-5 shadow-[var(--shadow-surface)]">
  <div className="text-xs text-[var(--color-text-muted)]">可用率</div>
  <div className="text-3xl font-extrabold tabular-nums text-[var(--color-text-primary)]">86.4%</div>
</div>

// ❌ 禁止
<div className="bg-black text-white shadow-2xl rounded-sm border-blue-500">
```

允许的「参考站同款」硬编码（仅当 token 未覆盖且与调研一致时）：

- brand 点：`#45bf78`
- 主文字：`#202829` / `#2d3435`
- 边框：`#dfe4e5`

---

## 组件清单（Upstream 应逐步具备）

1. `AppShell` — 顶栏 + 主内容最大宽（约 `max-w-7xl` 居中）
2. `ThemeToggle` — 切换 `data-theme`
3. `StatCard` — KPI
4. `Badge` / `StatusDot`
5. `Panel` / `SectionHeader`（标题 + 右侧「展开/收起」）
6. `DataTable`
7. `EmptyState` / `ErrorState`（用 danger/info token）
8. `BackLink` — 「返回 xxx 列表」

---

## 文案语气

- 用「监测 / 样本 / 综合倍率 / 可用率 / 最近检查 / 已核验」
- 少用「超强 / 史上最 / 一键躺平」等营销词
- 错误展示完整：`HTTP 503 · Service temporarily unavailable · request_id=...`

---

## Review 清单

做完 UI 后自检：

1. 是否像「比价雷达」而不是通用 Admin 模板？
2. 品牌绿是否只作点缀，而不是整页刷绿？
3. 深浅主题是否都可读？
4. 表格与 KPI 数字是否等宽对齐？
5. 失败/降级状态是否一眼可辨？

---

## 输出要求

当用户让你「按 priceai 风格做 X」时：

1. 先简述用到的布局模式与 token
2. 再改代码（优先复用 `apps/web/src/components/*`）
3. 结束时列出对齐点与偏差

参考调研原文：`design/priceai-style.md`
