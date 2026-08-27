# apps/web — Agent 规范（Vue 3，已迁移完成）

> 范围：`apps/web/`（Vue 3 + Vite 7 + Tailwind 4 + vue-router，暖纸+浓墨风格控制台）。
> 上位规则见 `../../AGENTS.md`，本文件只讲 Vue 前端层的目录、公共层抽取、组件 / composable / api 客户端契约，以及禁止项。
> 当前状态：**React → Vue 迁移已完成**（2026-08 复核：`src/` 下无任何 `.tsx`，`package.json` 无 react 依赖）。本文件描述的就是现状，不是目标态。

---

## 1. 技术栈

- **框架**：Vue 3.5 + `<script setup lang="ts">` + Composition API；**不使用 React**。
- **路由**：`vue-router@4`，`createWebHistory`；路由表集中放在 `src/router/index.ts`。登录不是独立路由——`LoginPage` 由 `App.vue` 按鉴权状态条件渲染。
- **构建**：Vite 7 + `@vitejs/plugin-vue` + `@tailwindcss/vite`；路径别名 `@` → `src`；`/api` 代理到 `${VITE_API_PROXY_TARGET || http://127.0.0.1:8000}`。类型检查走 `vue-tsc --noEmit -p tsconfig.json`（`npm run build` 自带；不要改回 `vue-tsc -b`——build 模式在本机会无限挂起，且 composite 会把 `vite.config.js` 编译进项目根目录，遮蔽 `vite.config.ts` 导致 dev server 丢失 vue 插件）。
- **样式**：Tailwind 4 + 设计 token 在 `src/styles/tokens.css`；主题为「暖纸 + 浓墨」（页底 `#f4f1ea`、品牌 veridian 绿 `#2c8a5a`），暗色经 `html[data-theme=dark]` 切换。组件层只消费语义工具类（`bg-panel` / `text-ink-strong` / `border-line` …），**不要**写死 hex 或 `bg-[var(--color-…)]`。
- **图标**：`lucide-vue-next`；不要引入其它图标库。
- **状态管理**：**不引 Pinia / Vuex**。跨页面共享状态用模块级 composable（`useConsoleData`）或 `provide/inject`（`useAppActions`）。
- **类型**：TypeScript 5.8 + `vue-tsc`；API 类型唯一来源 `src/lib/types.ts`，不要复制到 `src/lib/api/types.ts`。
- **包管理**：`npm`；`package-lock.json` 入库。

---

## 2. 实际目录

```
apps/web/src/
├── main.ts                 # createApp + router + tokens.css + 主题预置
├── App.vue                 # 根组件：鉴权分支 / AppShell / 全局弹窗 / provideAppActions
├── router/index.ts         # 路由表（懒加载）；无守卫，401 靠事件驱动
├── components/
│   ├── ui/                 # 公共 UI 原子件（index.ts 统一出口 + colorTokens）
│   │   ├── Button.vue / ConfirmDialog.vue / Field.vue / Input.vue
│   │   ├── Modal.vue / Select.vue / Spinner.vue / SwitchRow.vue
│   │   ├── Tabs.vue / Textarea.vue / index.ts
│   ├── AppShell.vue        # 顶栏 + 主内容容器（含移动端导航）
│   ├── PageHeader.vue / Panel.vue / StatCard.vue / Badge.vue
│   ├── ChangeTable.vue / ChangeValue.vue
│   ├── SiteTable.vue / SiteFormDialog.vue / RatiosDialog.vue
│   ├── ChannelDiscoveryPanel.vue / ChannelPriorityDialog.vue
│   ├── AdminSiteFormDialog.vue
│   ├── Sub2ApiChannelDialog.vue / Sub2ApiChannelTable.vue / Sub2ApiPricingEditor.vue / Sub2ApiNumberField.vue
│   ├── MainSiteHealthPanel.vue
│   ├── GroupSummaryBar.vue / PerfBars.vue / ModelCell.vue / NewApiModelRow.vue
│   ├── LoginPage.vue       # 由 App.vue 条件渲染（非路由）
│   └── ToastViewport.vue   # 全局 toast 渲染；触发靠 useToast()
├── composables/            # 唯一公共逻辑层
│   ├── useAuth.ts          # 三态鉴权单例 + console-unauthorized 监听
│   ├── useToast.ts         # 全局 toast 单例 + errorText 工具
│   ├── useConsoleData.ts   # 站点/变化/推送设置共享数据（15s 轮询）
│   ├── useReconcileMode.ts # 主站对账模式（停用/删除）
│   ├── useBalances.ts      # 账户额度按需查询
│   ├── useAppActions.ts    # provide/inject 的 App 级动作
│   └── useTheme.ts         # 明暗主题
├── lib/
│   ├── api/                # 数据层：按域拆分的 api 客户端（见 §6）
│   │   ├── adminSites.ts / auth.ts / client.ts / index.ts
│   │   ├── monitoring.ts / notifications.ts / sessionSync.ts / settings.ts
│   ├── automaticRefresh.ts / browserSessionBridge.ts / channelPriority.ts
│   ├── format.ts           # fmtTime / platformLabel / ratioLabel / usd / truthy 等纯函数
│   ├── mainSiteHealth.ts / perf.ts / sub2apiChannel.ts / upstreamError.ts
│   └── types.ts            # 唯一前端类型来源
├── pages/                  # 路由页面（全 .vue）
│   ├── OverviewPage.vue / SitesPage.vue / DetailPage.vue / ChangesPage.vue
│   ├── BalancePage.vue / ChannelsPage.vue / NotificationsPage.vue
├── styles/tokens.css       # 设计 token 权威文件
└── vite-env.d.ts
```

---

## 3. 公共 UI 组件（`components/ui/`）

> 边界：**`ui/` 禁止依赖 `composables/` / `lib/api/` / 任何 `lib/*` 业务工具**；越界就下沉到 `components/` 根目录或 `composables/`。
> 所有页面 / 业务组件**只**从 `components/ui`（index.ts 出口）import，**不要**直接 import 单个 `.vue` 文件。

| 组件 | 职责 |
|------|------|
| `Button.vue` | 主/次/危险三态；loading / disabled |
| `Input.vue` / `Textarea.vue` / `Select.vue` | 表单控件；`v-model` 双向绑定 |
| `Field.vue` | label + 控件 + help 三段式 |
| `SwitchRow.vue` | 行内开关 + 标题 |
| `Modal.vue` | `<Teleport to="body">` + `v-model:open`；内容 slot |
| `ConfirmDialog.vue` | 标题 / 内容 / busy / error；emit `confirm` / `cancel` |
| `Tabs.vue` | tab 切换 |
| `Spinner.vue` | 统一 loading 圈 |

**规则**

- props 用 `defineProps<...>()` 强类型；事件用 `defineEmits<...>()`；不要 `any`。
- 不在 ui 组件内 fetch / onMounted 拉数据；外部数据由父组件传入。
- 弹窗族用 `v-model:open` + `<Teleport to="body">`；关闭时清理本地状态。
- 颜色一律走语义工具类 / `colorTokens`；不要在 `ui/index.ts` 之外再 export 一份颜色表。

---

## 4. 业务组件（`components/<Name>.vue`）

依赖 composables 或 api、被多页面复用的组件放这里；**不要**自建 `components/business/` 子目录。

| 组件 | 说明 |
|------|------|
| `AppShell.vue` | 顶栏导航 + 移动端抽屉 + `<main>` 容器；由 `App.vue` 引入 |
| `PageHeader.vue` / `Panel.vue` | 页头（标题+操作区 slot）/ 白卡片面板 |
| `StatCard.vue` | KPI 卡（label / value / hint / icon / tone） |
| `Badge.vue` | 状态胶囊（neutral/success/warning/danger/info/brand，可选 dot） |
| `SiteTable.vue` | 渠道列表（平台折叠分组、行内检测/同步、「更多」菜单） |
| `SiteFormDialog.vue` / `RatiosDialog.vue` | 监控站点新增编辑 / 分组倍率弹窗 |
| `ChannelDiscoveryPanel.vue` | 主站渠道 → 监控站点发现导入 |
| `ChannelPriorityDialog.vue` | 渠道优先级编辑 |
| `AdminSiteFormDialog.vue` | 主站 CRUD 表单 |
| `Sub2ApiChannel*` / `Sub2ApiPricingEditor.vue` | sub2api 渠道表格 / 编辑 / 计费阶梯 |
| `MainSiteHealthPanel.vue` | 总览页主站健康区块 |
| `ChangeTable.vue` / `ChangeValue.vue` | 变化记录渲染 |

**规则**

- 业务组件允许 import `composables/` 和 `lib/api/`；但**不要**直接 import 另一个业务组件（避免环依赖）；要复用就抽 composable。
- props 优先用 `lib/types.ts` 的类型别名；透传字段用 `[key: string]: unknown` 兜底，不用 `any`。
- 本地状态留在 `<script setup>`，跨组件共享才上 composable。

---

## 5. 公共逻辑层（`composables/`）

命名 `useXxx.ts`，统一返回 ref / computed / 显式 action。

| composable | 职责 |
|------------|------|
| `useAuth.ts` | 三态 `authReady / authRequired / authed`（模块级单例）；监听 `console-unauthorized`；提供 `setAuthed / handleLogout` |
| `useToast.ts` | 全局 toast（success/error/info/run）；导出 `errorText(err)` |
| `useConsoleData.ts` | sites / changes / notify 共享数据；App.vue 传 `enabled` 激活拉取 + 15s 轮询；页面不传 enabled 只读 |
| `useReconcileMode.ts` | 主站对账模式（disable/delete），含删除模式二次确认状态 |
| `useBalances.ts` | 账户额度按需查询（每使用方独立实例，非单例） |
| `useAppActions.ts` | App 级动作注入（打开站点表单/倍率弹窗/删除确认/检测/同步等），供页面 inject |
| `useTheme.ts` | light/dark 切换；写 `localStorage.upstream-theme` + `html[data-theme]` |

**规则**

- composable 内不允许直接 fetch URL；统一走 `lib/api/<domain>`。
- 401 / 登出的副作用只在 `useAuth` 处理；其他 composable 不要重复监听 `console-unauthorized`。
- 定时器 / window 事件监听必须在 `onUnmounted`（或 watch `onCleanup`）清理。
- 状态默认 `shallowRef` / `ref<T[]>`；不要把整个响应体塞 `reactive`。

---

## 6. 数据层（`lib/api/`）

按域拆分，每个文件对应后端一个 router；新加端点先在 `backend/api/routers/<domain>.py` 加好，再回到前端加方法，并同步 `lib/types.ts` 类型与对应页面。

| 前端模块 | 后端 router | 主要端点 |
|----------|-------------|----------|
| `auth.ts` | `auth.py` | `GET /api/auth/status`、`POST /api/auth/login`、`POST /api/auth/logout` |
| `monitoring.ts` | `monitoring.py` | `/api/sites`、`/api/sites/{id}/changes|snapshots|discovery-links|account|check...`、`/api/sites/sync`、`/api/check-*` |
| `notifications.ts` | `notifications.py` | settings / logs / test-email / test-wecom |
| `settings.ts` | `settings.py` | `GET/PUT /api/settings`（对账模式） |
| `sessionSync.ts` | `session_sync.py` | `/api/*/session-sync/requests...` |
| `adminSites.ts` | `admin_sites.py` | `/api/admin/sites` 及渠道 CRUD / 匹配 / key 刷新 |

**`client.ts`**：`request<T>()` 自动注入 Bearer token；401（非 auth 路径）→ 清 token + 广播 `console-unauthorized`；JSON 解析失败兜底提示。

**`index.ts`**：合并各域为 `api` 对象。页面 / 组件**只** `import { api } from '@/lib/api'`，不要直连域文件。

**`lib/format.ts`**：跨页面复用的纯函数（`fmtTime` / `platformLabel` / `ratioLabel` / `usd` / `truthy` / 变化类型文案与配色…）。金额格式化只用这里的 `usd()`，不要再写局部 `fmtUsd`。

**`lib/types.ts`**：与后端 schemas 一一对应；透传字段用 `[key: string]: unknown`，禁 `any`。

---

## 7. 页面（`pages/`）

| 页面 | 路由 | 关键依赖 |
|------|------|----------|
| `OverviewPage.vue` | `/` | `useConsoleData` / `SiteTable` / `ChangeTable` / `MainSiteHealthPanel` |
| `ChannelsPage.vue` | `/channels` | 主站选择 + 渠道表格 + 分组视角侧栏 + Channel* 弹窗 |
| `SitesPage.vue` | `/sites` | `useConsoleData` / `SiteTable` / 同步主站 |
| `DetailPage.vue` | `/detail`, `/detail/:id` | 快照 / 来源关联 / 账户额度 / 历史变化 |
| `ChangesPage.vue` | `/changes` | `useConsoleData` / `ChangeTable` |
| `BalancePage.vue` | `/balance` | `useBalances` |
| `NotificationsPage.vue` | `/notifications` | 推送设置表单 + 测试发送 + 推送日志 |
| （登录） | 无路由 | `LoginPage` 由 `App.vue` 在未鉴权时渲染 |

**规则**

- 页面只做编排：拉数据 → 传业务组件 → 监听事件 → 调 `api` / composables。
- 页面内禁止 `fetch('/api/...')`；统一 `await api.xxx()`。
- 弹窗状态由页面/App 持有；跨层动作走 `useAppActions` 注入，不要 props 层层透传。

---

## 8. 路由（`router/`）

- 路由表集中在 `src/router/index.ts`，全部懒加载；`/:pathMatch(.*)*` 重定向 `/`。
- **没有路由守卫**：鉴权是「App.vue 按 `useAuth` 三态切换渲染」，401 经 `console-unauthorized` 事件把 `authed` 打回 false 即回到登录视图。
- 新加路由时同步更新 `AppShell.vue` 导航数组与本表。

---

## 9. 鉴权与浏览器同步约定

- `localStorage.console_token` 存 Bearer token；`lib/api/client.ts` 是唯一读写入口。
- 401 事件名固定 `console-unauthorized`；只有 `useAuth` 监听。
- 浏览器同步终态机集合与后端严格一致；展示文案见 `SiteTable.sessionSyncLabel`。
- 两类站点（监控 `sites` vs 主站 `admin_sites`）不可混淆；详见根 `AGENTS.md`。

---

## 10. 维护约定

- 改 UI 先看 `styles/tokens.css` 的注释（token 语义、档位、动效时长都写在里面）；新样式优先复用现有 token / 工具类，不新增 hex。
- UI 改动必须在浏览器 dev 模式过一遍主流程（浅色 + 暗色都要看），不能只靠 typecheck 就声称完成。
- 删除文件前先全局搜引用（`rg "<Name>|from .*<name>"`），确认 0 引用再 `rm`；不留「以防万一」的死代码和 `// removed` 注释，历史交给 git。
- `dist/` / `node_modules/` / `tsconfig.tsbuildinfo` 已 gitignore，不要提交。
- 默认不新增测试文件。

---

## 11. 不要做

- **不要写 React**（`.tsx` / `lucide-react` / `react-router-dom` / 任何 `react*` 依赖）。
- **不要在 `components/ui/` 引入 `composables/` / `lib/*`**；越界就下沉。
- **不要**绕过 `components/ui/index.ts` 直连单个 ui 组件文件，或另建第二出口。
- **不要**在页面 / `App.vue` 内 `fetch('/api/...')`；统一 `import { api } from '@/lib/api'`。
- **不要**在 composable 里 `setInterval` / `addEventListener` 后忘记清理。
- **不要**引入 Pinia / Vuex；状态共享用 composable 单例或 provide/inject。
- **不要**在 `lib/types.ts` 用 `any`；用 `[key: string]: unknown`。
- **不要**在 `lib/api/` 之外建 `src/api/` 复制一份。
- **不要**重复实现已有纯函数（时间/金额/倍率/状态文案都在 `lib/format.ts`，先搜再写）。
- **不要**新增测试；用户没要就别加。
- **不要**在主站 `id=2`（`aiinfinite.online`）相关流程加测试覆盖（事故复盘见根仓库记忆）。
- **不要**绕过 `useAppActions` 把 dialog state 用 props 跨多层传递。
