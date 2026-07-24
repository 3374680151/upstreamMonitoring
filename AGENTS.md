# Upstream — Agent Global Rules

> 本文件是本仓库对所有 AI Agent（Grok / Claude / Codex / OpenClaw / Hermes 等）的**全局工作规则**。  
> 开始任何实现前，先读本文件与 `docs/product.md`，UI 实现前必须加载 skill `priceai-ui`。

---

## 1. 项目是什么

**Upstream** 是一套「AI 中转 / 上游供给」控制面与观测台：

- 管理上游渠道、模型、分组、倍率与密钥
- 观测可用性、延迟、成功率、错误码（含 503 等失败请求）
- 记录请求日志：成功与失败都要可查（解决「客户端 503 但 usage 无记录」类问题）
- 控制台 UI 对标 [PriceAI 中转站详情页](https://priceai.cc/api-transit/ai-rtoc-cc) 的信息密度与视觉语言

本仓库当前阶段：**脚手架 + 设计系统 + 产品边界**。先打稳 UI 与领域模型，再逐步实现后端能力。

---

## 2. 全局强制规则

### 2.1 语言与沟通

- 对用户默认使用**简体中文**
- 代码标识符、API path、git commit 消息可用英文；用户可见文案用中文
- 提交说明写完整句子，说明「改了什么、为什么」

### 2.2 改动边界

- **只改任务相关文件**，不做无关重构、不顺手「清理」大范围代码
- 不擅自新增用户未要求的文档/依赖，除非任务明确需要
- 密钥、Token、Cookie **禁止**写入仓库；用 `.env.example` 占位
- 不实现攻击性 exploit / 恶意软件；漏洞修复仅限本仓库防御面

### 2.3 工作方式

1. 先读 `AGENTS.md` → `docs/product.md` → 相关模块 README
2. UI 任务：先读 skill `priceai-ui`（`.agents/skills/priceai-ui/SKILL.md`）
3. 小步提交；不确定的产品决策先问用户
4. 破坏性操作（force push、删库、改共享配置）先确认

### 2.4 技术栈约定（脚手架）

| 层 | 选型 | 说明 |
|----|------|------|
| Web | Vite + React + TypeScript + Tailwind CSS v4 | 控制台 |
| 样式 | CSS 变量 + Tailwind utility | token 在 `apps/web/src/styles/tokens.css` |
| 图标 | lucide-react | 与 PriceAI 同类线性图标 |
| API（后续） | 待定（优先 Go 或 Node） | 先接口契约，后实现 |
| 数据（后续） | SQLite → Postgres | 本地优先可运行 |

未明确要求时，**不要**换成 Next.js / Ant Design / Element Plus / Material UI 等另一套视觉体系。

---

## 3. UI 强制规则（PriceAI 风格）

> 完整规范见 skill：`priceai-ui`。此处只列必须遵守的硬约束。

1. **必须**使用 `tokens.css` 中的语义色：`--color-page`、`--color-panel`、`--color-brand`、`--color-text-*`、`--color-border` 等；禁止随手引入高饱和彩虹色或纯黑大面积背景。
2. **默认浅色**；深色通过 `html[data-theme="dark"]` 切换，token 已双主题定义。
3. 品牌强调色为 **翠绿** `#45bf78`（dark 下可偏 `#65cc8c`），用于状态点、可用、成功、关键 CTA 强调。
4. 正文主色为冷灰绿炭色：`#202829` / `#2d3435`，**不是**纯黑 `#000`。
5. 页面底 `#f9f9f9`，卡片白底，细边框 `#dfe4e5`，圆角以 `rounded-lg` / `rounded-xl` / `rounded-2xl` 为主；状态胶囊多用 `rounded-full`。
6. 阴影极轻：基于 `#2d3435` 低透明度，禁止厚重 Material 阴影。
7. 数字用 `tabular-nums`；倍率/价格/延迟等关键指标可 `font-extrabold`。
8. 信息架构：顶栏导航 → 站点/渠道头图信息 → KPI 卡片行 → 可折叠价格表/分组表 → 监测样本表。
9. 文案风格：冷静、数据优先、少营销腔；状态词固定为「可用 / 已核验 / 不可用 / 降级 / 维护中」等。

**实现 UI 时 checklist：**

- [ ] 是否只用了设计 token（或 skill 中的等价 utility）？
- [ ] 是否同时考虑 light / dark？
- [ ] 是否有 KPI 条、表格、状态胶囊、返回链路？
- [ ] 失败态是否用 danger token，而不是默认红色硬编码？

---

## 4. 领域概念（实现时对齐）

| 概念 | 含义 |
|------|------|
| Channel / 渠道 | 一条上游供给（如某中转站、官方 API） |
| Group / 分组 | 渠道内计费或路由分组（如 GPT / GPT Pro） |
| Model | 对外暴露的模型 ID |
| Rate / 倍率 | 相对官方价的综合倍率 |
| Probe / 监测样本 | 主动探测或公开快照得到的可用性样本 |
| Request Log | 每次转发请求的记录（含 4xx/5xx） |
| Usage | 计费用量；**不得**替代错误日志 |

硬需求：**失败请求必须可查询**（status、error type、upstream latency、request id），不能只在成功扣费后才落库。

---

## 5. 目录约定

```text
upstream/
  AGENTS.md                 # 本文件：全局规则
  README.md
  docs/product.md           # 产品范围与里程碑
  design/priceai-style.md   # UI 风格调研摘要
  .agents/skills/priceai-ui # UI skill（权威）
  apps/web                  # 控制台前端
  apps/api                  # 后端（后续）
  scripts/                  # 工具脚本
```

---

## 6. 当前阶段目标

1. 控制台壳：顶栏、主题切换、路由占位
2. 渠道详情页骨架：KPI + 分组表 + 样本表（可用 mock）
3. 设计 token 与组件 primitives（Badge / StatCard / DataTable / Panel）
4. 后续：鉴权、上游 CRUD、探测任务、请求日志（含 503）

---

## 7. 完成任务时的汇报格式

- 改了哪些路径
- 如何本地预览（命令）
- 与 PriceAI 风格对齐点（1–3 条）
- 未做事项 / 风险

---

**一句话：数据密、界面净、翠绿点缀、失败可观测。**
