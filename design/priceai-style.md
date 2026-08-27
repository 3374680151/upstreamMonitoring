# PriceAI UI 风格调研

- 源站：https://priceai.cc
- 范本页：https://priceai.cc/api-transit/ai-rtoc-cc?back=family%3Dgpt%26sort%3Drate
- 调研日：2026-07-25
- 技术观察：Next.js App Router + Tailwind（含 CSS 变量主题）+ lucide 图标

## 页面信息架构

1. **全局顶栏**：Logo「PriceAI」、产品定位「AI 比价雷达」、主导航（卡网订阅 / 官方订阅 / 官方 API / 中转 API）、主题切换、社区入口、账户
2. **返回链路**：「返回中转站列表」
3. **渠道头区**：名称、优惠/技术栈徽章（New API）、数据状态（已核验 + 更新日期）、一段能力说明
4. **KPI 横条**：ChatGPT 综合倍率、Claude 综合倍率、可用率、最近检查
5. **运营条**：可用优惠、反馈入口
6. **可折叠价格表**：按模型家族（ChatGPT / Claude / Grok / 图片）分组；内含综合倍率趋势、分组表、监测模型价格明细
7. **监测样本表**：可用状态、样本数、区间、延迟、来源说明

## 视觉关键词

- 冷灰绿炭色文字体系（`#202829` / `#2d3435`）
- 页面浅灰底 `#f9f9f9`，卡片纯白
- 品牌翠绿 `#45bf78` 作状态点与成功强调
- 细边框 `#dfe4e5`，轻阴影（基于 `#2d3435` 低透明）
- 大量 `rounded-full` 胶囊标签 + `rounded-2xl` 卡片
- 数字 `tabular-nums` + `font-extrabold`
- 双主题：`html[data-theme=dark]` 转为深青灰

## 与 Upstream 的映射

| PriceAI | Upstream |
|---------|----------|
| 中转站详情 | 渠道 / 上游详情 |
| 综合倍率 | 路由分组倍率 / 成本系数 |
| 可用率 / 样本 | 探测成功率 / 请求样本 |
| 监测样本 | Request Log + Probe 结果（含 503） |
| 价格表折叠区 | 模型与分组配置表 |

## 刻意不做

- 不复刻 PriceAI 的商业比价爬虫业务
- 不抄其品牌 Logo / 文案版权内容
- 只复用**视觉语言与信息密度模式**


---

# 全量渠道 key / 倍率刷新 —— 设计文档

> 分支：`feat/full-key-refresh-batch-20260827`（commits `1318ce1` / `3feb9d3`）
> 目标：一次操作完成「全部渠道 key + 上游分组倍率」的并发刷新，不再逐渠道手点。

---

## 1. 背景与问题

### 问题 1：添加主站 + 2FA 后展示不完整
- 旧版添加模式禁用 2FA 输入，必须保存 → 重开编辑弹窗 → 再填 2FA。
- 2FA 验证成功后只刷 **1 个**渠道 key，其余渠道长期处于「缺少渠道 key」。
- 即使开了自动 key 同步，也是每 5 分钟一轮、每轮 3 个的慢速 drip（19 渠道要 30+ 分钟）。

### 问题 2：刷新 key / 倍率没有全局入口
- 只有逐渠道 `POST /channels/{cid}/key/refresh`，前端每行一个按钮，必须一个个点。
- 倍率匹配同样只能逐渠道触发。

---

## 2. 核心设计：统一「全量刷新批次」机制

两种批次共用同一套后端状态机与并发执行器，仅 mode 不同：

| mode | 名称 | 平台 | 做什么 | 是否需要 2FA |
|------|------|------|--------|--------------|
| `key` | 全量渠道 key 刷新 | 仅 NewAPI | 每渠道「读 key → 重匹配倍率」 | 是（proof） |
| `ratio` | 全量倍率刷新 | NewAPI + sub2api | 每渠道只重匹配上游分组倍率（复用已存 key） | 否 |

### 2.1 批次状态机（进程内存态）

```
trigger ──► running ──► done
              │
              ├─► paused   （proof 失效：需重新验证 2FA，重验后 re-trigger 断点续跑）
              └─► failed   （主站删除 / 渠道列表读取失败）
```

- 状态存 `core/state.py` 的 `ADMIN_KEY_REFRESH_BATCHES: Dict[admin_site_id, batch]`，锁 `ADMIN_KEY_REFRESH_BATCH_LOCK`。
- 进程重启即失效（内存态），重新触发即可。
- **断点续跑仅同 mode 复用**：paused 的 key 批次不会被 ratio 触发错误接管。

### 2.2 并发执行器（`sync_service._run_admin_key_refresh_batch`）

一次性 daemon 线程 + `ThreadPoolExecutor(max_workers=4)`：

- **key 模式快速门控**：`MAIN_CHANNEL_KEY_FAST_GATES` 命中时，key 接口最小间隔从 2s 降到 0.25s（预约式错峰）；批次结束（含异常，finally 兜底）必须关闭。
- **预约式限速**：锁内只做「冷却检查 + 间隔预约」，HTTP 请求挪到锁外，多线程据此错峰并发；日常单渠道请求路径不变（仍 2s）。
- **429 冷却**：触发限流 → 30s 冷却，worker 原地重试（最多 3 次/渠道），仍失败则计 failed 跳过。
- **proof 暂停**：任一 worker 返回 needs-verification → 批次置 paused，取消其余 pending future。
- **倍率批次开始清空 `NEWAPI_MATCH_GROUPS_CACHE`**（30s TTL）：保证拿到最新倍率；随后同上游多渠道仍共享一次分组请求。

### 2.3 API 契约

| 端点 | 作用 |
|------|------|
| `POST /api/admin/sites/{id}/channels/keys/refresh` | 单站 key 批次（NewAPI） |
| `POST /api/admin/sites/{id}/channels/ratios/refresh` | 单站倍率批次（全平台） |
| `POST /api/admin/sites/keys/refresh-all` | 全部 NewAPI 主站 key 批次 |
| `POST /api/admin/sites/ratios/refresh-all` | 全部主站倍率批次 |
| `GET /api/admin/sites` | 每站附带 `key_refresh` / `ratio_refresh` 进度（无批次不返回字段） |

进度字段：`{status, mode, total, done, failed, message, started_at, updated_at}`。

### 2.4 前端交互

- **AdminSiteFormDialog**：添加模式启用 2FA 输入；保存 → 创建 → 自动验证 2FA → 自动触发 key 批次 → 自动关窗（失败保留弹窗供重试，站点已保存）。
- **ChannelsPage**：`刷新全部 key`（NewAPI）/ `刷新倍率`（全平台）按钮 + 双进度胶囊「key 刷新中 x/N」/「倍率刷新中 x/N」，10s 轮询，完成自动重载数据。
- **SitesPage**：`刷新全部主站 key` / `刷新全部主站倍率` 全局按钮，结果写入同步结果面板。
- 2FA 验证成功（编辑弹窗）同样触发全量 key 批次。

---

## 3. 倍率数据链路（渠道页 vs 监控页）

**两条独立链路，非同一套接口，非同一类逻辑：**

| | 渠道页倍率显示 | 监控页倍率变化 |
|---|---|---|
| 数据来源 | 渠道 key → **上游用户视角**（NewAPI 用户 token 接口 / sub2api 登录态分组） | **站点视角**全量分组快照 |
| 写入表 | `channel_upstream_bindings.matched_groups` | `snapshots` + diff → `changes` |
| 触发 | 手动/批次/同步（非变化驱动，每次写当前值） | 定时检测 + 手动检测（**变化驱动**，diff 出 `ratio_changed` 才记录） |
| 前端接口 | `/api/admin/sites/{id}/channels` + `channel-mappings` | `/api/changes`、`/api/sites` |

**结论**：渠道页倍率不会自动跟随上游变化，需重新匹配（点「刷新倍率」/「刷新全部 key」/同步主站）；监控页变化记录由检测 diff 产生。分组名/倍率两边可能一致，但数据各自独立。

---

## 4. 快速上手

### 4.1 启动

```bash
# 后端（读 .env，含 MySQL）
python3 app.py                        # http://127.0.0.1:8000

# 前端开发
cd apps/web && npm run dev            # http://localhost:5173

# 生产
cd apps/web && npm run build && cd ../.. && python3 app.py
```

健康检查：`curl http://127.0.0.1:8000/healthz` → `{"status":"ok"}`

### 4.2 典型操作流

**A. 新增一个 NewAPI 主站（一步到位）**
1. 渠道页（或站点页主站区）→ 添加主站 → 填名称 / Base URL / 登录方式。
2. **在同一弹窗里直接填 2FA 验证码** → 保存。
3. 系统自动：创建站点 → 验证 2FA → 启动全量 key 批次 → 关窗。
4. 渠道页看到「key 刷新中 x/N」胶囊，完成后每个渠道带真实 key + 匹配倍率。

**B. 日常刷新（渠道页，作用于当前选中主站）**
- 「刷新全部 key」：并发读全部渠道 key + 重匹配倍率（NewAPI 专属，4 线程，19 渠道约 10–60s，受 0.25s 错峰 + 429 冷却保护）。
- 「刷新倍率」：只重匹配倍率，复用已存 key，**免 2FA**，全平台可用（实测 19 渠道约 8s）。

**C. 全局刷新（站点页，作用于所有主站）**
- 「刷新全部主站 key」：所有 NewAPI 主站各起一个 key 批次。
- 「刷新全部主站倍率」：所有主站（含 sub2api）各起一个倍率批次。
- 结果面板逐站显示启动情况；进度仍到渠道页看胶囊。

### 4.3 状态胶囊含义

| 显示 | 含义 | 处理 |
|------|------|------|
| `key 刷新中 12/19` | 批次运行中 | 等待，10s 自动刷新 |
| `key 刷新已暂停，需重新验证 2FA` | proof 失效 | 主站编辑弹窗重新验证，或再点一次按钮断点续跑 |
| `key 刷新完成 19/19` | 全部成功 | 自动重载数据 |
| 红色失败提示 | 某渠道失败（hover 看消息） | 检查该渠道上游 / 登录态 |

### 4.4 注意事项

- key 批次仅 NewAPI（2FA proof 是 NewAPI 专属机制）；sub2api 只用倍率批次。
- 批次进度是进程内存态：后端重启后进度消失，重新触发即可。
- 同一主站同 mode 批次运行中重复触发会返回 `already_running`，不会重复跑。
- 触发 429 时批次自动冷却 30s 重试，不会打挂上游。
