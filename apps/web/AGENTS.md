# apps/web — Agent 规范（Vue 3，迁移进行中）

> 范围：`apps/web/`（Vue 3 + Vite 7 + Tailwind 4 + vue-router，PriceAI 风格控制台）。
> 上位规则见 `../../AGENTS.md`，本文件只讲 Vue 前端层的目录、公共层抽取、组件 / composable / api 客户端契约，以及禁止项。
> 当前状态：迁移进行中；旧的 React 文件正被 Vue 3 替换。**不要在未确认替换行为前删除旧文件**；一旦新版本就绪就立即清掉旧版本。

---

## 1. 技术栈（已切到 Vue 3）

- **框架**：Vue 3.5 + `<script setup lang="ts">` + Composition API；**不再使用 React**。
- **路由**：`vue-router@4`，`createWebHistory`；路由表集中放在 `src/router/index.ts`（现状单文件，目标可拆 `routes.ts`，但不要在 `main.ts` 内手写跳转）。
- **构建**：Vite 7 + `@vitejs/plugin-vue` + `@tailwindcss/vite`；路径别名 `@` → `src`；`/api` 代理到 `${VITE_API_PROXY_TARGET || http://127.0.0.1:8000}`。
- **样式**：Tailwind 4；色 token 在 `src/styles/tokens.css`（含 `colorTokens` 导出，对应 CSS 变量）；暗色通过 `html[data-theme=dark]` 切换。
- **图标**：`lucide-vue-next`（已在 `package.json`）；**不要**回退到 `lucide-react` 或在 `package.json` 里加 `react*`。
- **状态管理**：**不引 Pinia / Vuex**。跨页面共享状态用 `provide/inject` + composable（`provideAppActions` 已在 `composables/useAppActions.ts`）。
- **类型**：TypeScript 5.8 + `vue-tsc`；API 类型在 `src/lib/types.ts`（与后端 `backend/api/schemas/*` 一一对应），不要把类型再复制一份到 `src/lib/api/types.ts`。
- **包管理**：`npm`；`package-lock.json` 入库；**不要**切到 pnpm / yarn / bun，也不要往 `package.json` 加 `react` / `react-dom` / `react-router-dom`。
- **测试**：默认不新增；`tests/web/*.test.mjs` 是历史 Playwright 套件，等迁移窗口提示后再决定保留 / 重写 / 删除。

---

## 2. 实际目录（与目标态的差距）

当前 `src/` 真实状态（迁移半成品）：

```
apps/web/src/
├── main.ts                 # Vue 入口（createApp + router + tokens.css + 主题）
├── App.vue                 # 新根组件（装配 AppShell / LoginPage / 弹窗 / 注入 actions）
├── App.tsx                 # ⚠ 旧 React 根组件，待删
├── main.tsx                # ⚠ 旧 React 入口，待删
├── router/
│   └── index.ts            # createRouter + 路由表
├── components/
│   ├── ui/                 # 公共 UI（index.ts 统一出口，colorTokens 在此）
│   │   ├── Button.vue / ConfirmDialog.vue / Field.vue / Input.vue
│   │   ├── Modal.vue / Select.vue / Spinner.vue / SwitchRow.vue
│   │   ├── Tabs.vue / Textarea.vue / index.ts
│   ├── AppShell.vue / .tsx
│   ├── Badge.vue / .tsx
│   ├── ChangeTable.vue / .tsx
│   ├── ChangeValue.vue
│   ├── LoginPage.vue / .tsx
│   ├── PageHeader.vue / .tsx
│   ├── Panel.vue / .tsx
│   ├── StatCard.vue / .tsx
│   ├── ToastViewport.vue
│   ├── Toast.tsx           # ⚠ 旧 React toast
│   ├── AdminSiteFormDialog.tsx
│   ├── BalanceSummaryPanel.tsx
│   ├── ChannelDiscoveryPanel.tsx
│   ├── ChannelFormDialog.tsx
│   ├── ChannelPriorityDialog.tsx
│   ├── MainSiteHealthPanel.tsx
│   ├── RatiosDialog.tsx
│   ├── SiteFormDialog.tsx
│   ├── SiteTable.tsx
│   ├── Sub2ApiChannelDialog.tsx
│   ├── Sub2ApiChannelTable.tsx
│   ├── Sub2ApiPricingEditor.tsx
│   └── ui.tsx              # ⚠ 旧 React UI 出口
├── composables/            # Vue 版公共逻辑
│   ├── useAuth.ts
│   ├── useToast.ts
│   ├── useConsoleData.ts
│   ├── useReconcileMode.ts
│   ├── useBalances.ts
│   ├── useAppActions.ts
│   └── useTheme.ts
├── lib/                    # 工具 + 旧 React hook 残留 + api 客户端
│   ├── api/                # 旧位置：按域拆分的 api 客户端（仍在这里，迁完前不要新增 `src/api/`）
│   │   ├── adminSites.ts / auth.ts / client.ts / index.ts
│   │   ├── monitoring.ts / notifications.ts / sessionSync.ts / settings.ts
│   ├── automaticRefresh.ts
│   ├── browserSessionBridge.ts
│   ├── channelPriority.ts
│   ├── format.ts
│   ├── mainSiteHealth.ts
│   ├── perf.ts
│   ├── sub2apiChannel.ts
│   ├── types.ts            # 唯一前端类型来源（不要复制到 api/types.ts）
│   ├── upstreamError.ts
│   ├── useAuth.ts          # ⚠ 旧 React hook
│   ├── useBalances.ts      # ⚠ 旧 React hook
│   ├── useConsoleData.ts   # ⚠ 旧 React hook
│   └── useReconcileMode.ts # ⚠ 旧 React hook
├── pages/                  # ⚠ 仍是 React .tsx，Vue 页面未落地
│   ├── BalancePage.tsx / ChangesPage.tsx / ChannelsPage.tsx
│   ├── DetailPage.tsx / NotificationsPage.tsx
│   ├── OverviewPage.tsx / SitesPage.tsx
├── styles/tokens.css
└── vite-env.d.ts
```

迁移目标态（`App.vue` / `App.tsx` / `main.tsx` 互斥留一；`pages/` 全 `.vue`；`composables/` 唯一来源；`lib/api/` 按迁移窗口决定保留或挪到 `api/`）。

---

## 3. 公共 UI 组件（`components/ui/`）—— 抽取原则

> `components/ui/` 与 `components/<业务>.vue` 的边界：**`ui/` 禁止依赖 `composables/` / `lib/api/` / 任何 `lib/*` 业务工具**；否则下沉到 `components/` 根目录或 `composables/`。
> `components/ui/index.ts` 已经是统一出口（`Button / Input / Textarea / Select / Tabs / Field / SwitchRow / Modal / ConfirmDialog / Spinner`）；所有页面 / 业务组件**只**从这里 import，**不要**直接 `import` 单个 `.vue` 文件。

| 组件 | 旧 React 来源 | 职责 |
|------|---------------|------|
| `Button.vue` | `ui.tsx` 的 Button | 主/次/危险三态；loading / disabled |
| `Input.vue` / `Textarea.vue` / `Select.vue` | `ui.tsx` | 表单控件；`v-model` 双向绑定 |
| `Field.vue` | `ui.tsx` | label + 控件 + 错误信息三段式 |
| `SwitchRow.vue` | `ui.tsx` | 行内开关 + 标题 + 描述 |
| `Modal.vue` | `ui.tsx` | `<Teleport to="body">` + `v-model:open`；内容 slot |
| `ConfirmDialog.vue` | `ui.tsx` | 标题 / 内容 / 确认回调；emit `confirm` / `cancel` |
| `Tabs.vue` | `ui.tsx` | tab 切换 |
| `Spinner.vue` | 新增 | 统一 loading 圈 |

**抽取规则**

- props 用 `defineProps<...>()` 强类型；事件用 `defineEmits<...>()`；不要 `any`。
- **不**引用 `useAuth` / `useApi` / `lib/api` / 任何 `lib/*` 业务工具；外部数据由父组件传进来。
- **不**在组件内 `fetch` / `onMounted` 拉数据；只做渲染。
- 颜色 / 间距用 `tokens.css` 变量 + Tailwind 工具类；**不要**写死 hex，`colorTokens` 已经集中了 `var(--color-*)`。
- 弹窗族用 `v-model:open` + `<Teleport to="body">`；关闭时清理本地状态。

---

## 4. 业务组件（`components/<Name>.vue` 根目录）

依赖 composables 或 api，但仍被多页面复用。**与 ui 的边界**：依赖 `composables/*` 或 `lib/api/*`，就放 `components/` 根目录（当前目录布局，**不要**自建 `components/business/` 子目录，除非迁移窗口明确要求）。

| 组件 | 旧来源 | 说明 |
|------|--------|------|
| `AppShell.vue` | `AppShell.tsx` | 侧边栏 + 顶栏 + 路由出口；由 `App.vue` 引入 |
| `PageHeader.vue` | `PageHeader.tsx` | 页面标题 + 操作区 slot |
| `Panel.vue` | `Panel.tsx` | 白卡片 + 标题 + 右侧 slot（actions） |
| `Badge.vue` | `Badge.tsx` | 状态胶囊（ok/warning/failed/...）；只读 props |
| `StatCard.vue` | `StatCard.tsx` | KPI 单卡（label / value / 趋势） |
| `ChangeTable.vue` / `ChangeValue.vue` | `ChangeTable.tsx` | 变化列表渲染 |
| `SiteTable.tsx` | 同 .tsx | 列表 + 启停 + 删除 + 检测（待迁 Vue） |
| `SiteFormDialog.tsx` | 同 .tsx | 新增 / 编辑监控站点（待迁 Vue） |
| `RatiosDialog.tsx` | 同 .tsx | 分组倍率弹窗（待迁 Vue） |
| `ChannelDiscoveryPanel.tsx` / `ChannelFormDialog.tsx` / `ChannelPriorityDialog.tsx` | 同 .tsx | 渠道发现 / 编辑 / 优先级（待迁） |
| `AdminSiteFormDialog.tsx` | 同 .tsx | 主站 CRUD（待迁） |
| `Sub2ApiChannelDialog.tsx` / `Sub2ApiChannelTable.tsx` / `Sub2ApiPricingEditor.tsx` | 同 .tsx | sub2api 主站专项（待迁） |
| `BalanceSummaryPanel.tsx` | 同 .tsx | 账户额度（待迁） |
| `MainSiteHealthPanel.tsx` | 同 .tsx | 主站健康总览（待迁） |
| `LoginPage.vue` | `LoginPage.tsx` | 登录表单（独立路由 `/login`） |
| `ToastViewport.vue` | `Toast.tsx` 的容器 | 全局 toast 渲染；触发靠 `useToast()` |

**抽取规则**

- 组件内部允许 `import` 自 `composables/` 和 `lib/api/`；但**不要**直接 `import` 另一个业务组件（避免环依赖）；要复用就抽 composable。
- 业务组件的 props 优先用类型别名（来自 `lib/types.ts`），**不要**用 `any`。
- 业务组件的本地状态留在 `setup()`，跨组件共享才上 composable。

---

## 5. 公共逻辑层（`composables/`）

> Vue 3 的 composable ≈ React 的 hook；命名 `useXxx.ts`，统一返回 ref / computed / 显式 action。

| composable | 职责 |
|------------|------|
| `useAuth.ts` | 三态 `authReady / authRequired / authed`（模块级 `shallowRef`）；监听 `console-unauthorized` 事件；提供 `login / logout` |
| `useToast.ts` | 全局 toast；`toast.success / error / info`；导出 `errorText(err)` 工具 |
| `useConsoleData.ts` | 站点 / 变化 / 推送设置三件套；`enabled` 守门 |
| `useReconcileMode.ts` | 主站对账模式（disable/delete），含 `pendingDeleteMode` |
| `useBalances.ts` | 账户额度按需查询 |
| `useAppActions.ts` | App 级动作注入（`provideAppActions` + `appActionsKey`），供页面 `inject` 调用弹窗 / 操作 |
| `useTheme.ts` | 主题（light/dark）切换；写 `localStorage.upstream-theme` + `html[data-theme]` |

**抽取规则**

- composable 内**不允许** `fetch` 直接打 URL；统一走 `lib/api/<domain>` 模块。
- 涉及 401 / 登出的副作用在 `useAuth` 集中处理；其它 composable 只 `import { useAuth }` 取状态。
- 定时器 / `window` 事件监听在 `onUnmounted` 必须清理；**不要**泄漏 `setInterval` / `EventListener`。
- 状态对象默认 `shallowRef` / `ref`；列表用 `ref<T[]>([])`；**不要**把整个响应体塞 `reactive`。
- 模块级单例（如 `useAuth` 的 `shallowRef`）要可独立测试；**不要**塞运行时副作用的全局可变 dict。

---

## 6. 数据层（当前在 `lib/api/`，迁移期别再挪）

**按域拆分**，每个文件对应后端一个 router；新加端点先在 `backend/api/routers/<domain>.py` 加好，再回到前端 `lib/api/<domain>.ts` 加方法。

| 前端模块 | 后端 router | 主要端点 |
|----------|-------------|----------|
| `lib/api/auth.ts` | `backend/api/routers/auth.py` | `GET /api/auth/status`、`POST /api/auth/login`、`POST /api/auth/logout` |
| `lib/api/monitoring.ts` | `backend/api/routers/monitoring.py` | `/api/overview`、`/api/sites`、`/api/changes`、`/api/sites/{id}/...`、`/api/sites/sync`、`/api/sites/discovery-import`、`/api/check-connection`、`/api/check-login` |
| `lib/api/notifications.ts` | `backend/api/routers/notifications.py` | `/api/notifications/settings`、`/api/notifications/logs`、`/api/notifications/test-email`、`/api/notifications/test-wecom` |
| `lib/api/settings.ts` | `backend/api/routers/settings.py` | `GET/PUT /api/settings`（主站对账模式） |
| `lib/api/sessionSync.ts` | `backend/api/routers/session_sync.py` | `/api/sites/{id}/session-sync/requests...` |
| `lib/api/adminSites.ts` | `backend/api/routers/admin_sites.py` | `/api/admin/sites...` |

**`lib/api/client.ts`**

- 提供 `request<T>(path, options?)`；
- 自动注入 `Authorization: Bearer <token>`（`getConsoleToken`）；
- 收到 401（非 `/api/auth/*`）→ 清 token + `window.dispatchEvent(new CustomEvent('console-unauthorized'))`；
- 解析 JSON，HTML 兜底提示「接口返回了网页内容而不是 JSON」；
- 暴露 `getConsoleToken` / `setConsoleToken`。

**`lib/api/index.ts`**

- 合并各域到 `api` 对象；**App.vue / 页面 / 业务组件只 `import { api } from '@/lib/api'`**。
- **不要**再有任何页面 / 组件直接 `import` 域文件（`@/lib/api/monitoring` 等）。

**`lib/types.ts`（唯一前端类型来源）**

- TypeScript 类型与 `backend/api/schemas/*` 一一对应：`Site` / `GroupItem` / `Change` / `Overview` / `SiteSnapshot` / `SiteAccountResponse` / `SiteCheckResponse` / `AdminSite` / `Channel` / `ChannelUpstreamBinding` / `NotificationSettings` / `NotificationLog` / `SessionSyncStatus` / `SessionSyncRequest` / `SessionSyncResult`；
- 联合类型：`Platform = 'newapi' | 'sub2api'`、`AuthMode = 'password' | 'token' | 'browser'`；
- 后端 `CompatibilityModel`（`extra="allow"`）透传的字段用 `[key: string]: unknown` 兜底，**不要**收窄成编译错误，也**不要**用 `any`。

---

## 7. 页面（`pages/`）

> 当前 `pages/*.tsx` 仍是 React 版；迁移窗口按 Vue 3 写新 `.vue`，确认行为一致后 `rm` 旧 `.tsx`。

| 页面（目标 `.vue`） | 路由 | 关键依赖 |
|---------------------|------|----------|
| `OverviewPage.vue` | `/` | `useConsoleData` / `MainSiteHealthPanel` |
| `SitesPage.vue` | `/sites` | `useConsoleData` / `SiteTable` / `SiteFormDialog` |
| `DetailPage.vue` | `/detail` 或 `/detail/:id` | `RatiosDialog` / `ChangeTable` / `BalanceSummaryPanel` |
| `ChangesPage.vue` | `/changes` | `useConsoleData` / `ChangeTable` |
| `BalancePage.vue` | `/balance` | `useBalances` |
| `ChannelsPage.vue` | `/channels` | `useApi` + `Channel*` 业务组件 |
| `NotificationsPage.vue` | `/notifications` | 推送设置 + 测试发送 |
| `LoginPage.vue` | `/login` | `useAuth`（现已在 `components/LoginPage.vue`，迁移时视情况挪到 `pages/`） |

**页面规则**

- 页面只做编排：拉数据 → 传给业务组件 → 监听业务组件事件 → 调 `api` / `composables`。
- **不在**页面内 `fetch('/api/...')`；统一 `await api.xxx()`（`api` 已在 `lib/api/index.ts` 合并）。
- 页面级局部状态留在 `setup()`，跨页面才抽 composable。
- 弹窗用 `<component :is="..." v-model:open="..." />` 或 `v-if + <Teleport>`，状态由页面持有；复杂的多弹窗编排通过 `provideAppActions` 注入。

---

## 8. 路由（`router/`）

- 当前 `src/router/index.ts` 是单文件（路由表 + 守卫集中）；不要往 `main.ts` 里手写跳转。
- 路由懒加载：`component: () => import('@/pages/...')`。
- 守卫职责：未登录访问 `requiresAuth` 路由 → 跳 `/login`；已登录访问 `/login` → 跳 `/`；`console-unauthorized` 事件由 `useAuth` 监听并切回登录页。
- 新加路由时同步更新 `AppShell.vue` 侧边栏与本表。

---

## 9. 鉴权与浏览器同步约定

- `localStorage.console_token` 存放 Bearer token；`lib/api/client.ts` 是**唯一**读写入口。
- 401 事件名固定 `console-unauthorized`；`composables/useAuth.ts` 监听，其他模块**不要**重复监听。
- 浏览器同步终态机集合与后端 `core.state.SESSION_SYNC_TERMINAL_STATUSES` / `SESSION_SYNC_PAGE_FAILURES` 严格一致；前端展示文案来自 `SESSION_SYNC_PAGE_FAILURES` 的中文注释（`extensions/upstream-session-bridge` 扩展协同）。
- 两类站点（监控 `sites` vs 主站 `admin_sites`）**不可混淆**；详见根 `AGENTS.md` 与 `upstream-two-site-model` 记忆。

---

## 10. 迁移期与清理

- 迁移窗口逐项把 `.tsx` 翻成 `.vue` / composable / `lib/api` 方法，**先在 dev 模式跑一遍主流程**，再 `rm` 旧文件。
- 一旦新版就绪，**立即删除**：
  - `App.tsx`（保留 `App.vue`）
  - `main.tsx`（保留 `main.ts`）
  - `lib/useAuth.ts` / `lib/useBalances.ts` / `lib/useConsoleData.ts` / `lib/useReconcileMode.ts`（保留 `composables/` 对应文件）
  - `components/<Name>.tsx`（迁完即删对应 `.vue` 同名文件）
  - `pages/*.tsx`（迁完即删对应 `.vue` 同名文件）
  - `components/ui.tsx`（保留 `components/ui/index.ts`）
  - `components/Toast.tsx`（保留 `ToastViewport.vue` + `composables/useToast.ts`）
- **不要保留**「以防万一」的旧代码或 `// removed` 注释；历史由 git 记录。
- `tsconfig.tsbuildinfo` / `dist/` / `node_modules/` 已在 `.gitignore`，**不要**反着提交。

---

## 11. 不要做（按当前项目状态）

- **不要写 React 组件**（`.tsx`）、`useXxx` React hook、`react-router-dom`、`lucide-react`；本项目已迁 Vue 3。`package.json` 不要再加 `react` / `react-dom` / `react-router-dom` / `lucide-react` 依赖。
- **不要在 `components/ui/` 引入 `composables/` / `lib/api/` / `lib/*` 业务工具**；越界就下沉到 `components/` 根目录。
- **不要**在 `components/ui/index.ts` 之外，再建一个 `components/ui.tsx` 或 `components/ui/index.vue` 出口。
- **不要**在页面 / `App.vue` 内 `fetch('/api/...')`；统一走 `import { api } from '@/lib/api'`。
- **不要**在 composable 里 `setInterval` / `addEventListener` 后忘记 `onUnmounted` 清理。
- **不要**把 Pinia / Vuex 引入；状态用 `provide/inject` + composable（`useAppActions` 已示范）。
- **不要**在 `lib/types.ts` 用 `any` 收口后端透传字段；用 `[key: string]: unknown` 兜底。
- **不要**再在 `lib/api/` 之外建 `src/api/` 复制一份；数据层先收敛在 `lib/api/`，等迁移窗口整体迁完再决定要不要挪到 `src/api/`。
- **不要**把 `composables/` 里的 `useAuth / useConsoleData / useReconcileMode / useBalances` 与 `lib/` 下同名 React hook 混用；旧的 `lib/use*.ts` 在新版就绪后立即 `rm`，**不要**用 `// deprecated` 注释保留。
- **不要**把 `components/ui.tsx` 复活；`components/ui/index.ts` 是统一出口。
- **不要**新增测试；用户没要就别加（旧的 `tests/web/*.test.mjs` 不要去「顺手补一下」）。
- **不要**把 `dist/` / `node_modules/` / `tsconfig.tsbuildinfo` 提交。
- **不要**在主站 `id=2`（`aiinfinite.online`）相关流程加测试覆盖（事故复盘见 `main-site-aiinfinite-and-test-clobber-incident` 记忆）。
- **不要**在迁移半成品里「为了跑得通」写两套实现并 import 同一个旧文件；要么完全 Vue，要么完全 React，**不要**混用。
- **不要**让 `colorTokens` 在 `components/ui/index.ts` 之外再 export 一份（`tokens.css` 已声明 `var(--color-*)`）；新组件要颜色就 `import { colorTokens } from "@/components/ui"`。
- **不要**绕过 `useAppActions` 把 dialog state 通过 props 跨多层传递；需要的话注入到子组件。
