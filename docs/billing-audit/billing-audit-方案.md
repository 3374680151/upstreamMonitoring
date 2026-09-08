# 计费核对（billing-audit）设计方案

> 状态：**P1（三方对照）、P2（dashboard 聚合 + sub2api 上游核对 / 红色告警 / 倍率基准覆盖 / 官方价同步）均已实现**；P2 后半批实现与操作记录见 [p2-实现记录.md](./p2-实现记录.md)（分支 `billing-audit-p2`，待验证合入）
> 日期：2026-09-07（P2 增量 2026-09-08，操作记录见 [dashboard-实现记录.md](./dashboard-实现记录.md)）
> Apifox：项目 `8772928` 已推送本文第 6 节接口至云端目录 **「计费核对 → billing-audit」**（P2 增量：overview 的 `bucket_minutes`/`reason_code`/`time_buckets`/`upstream_breakdown` 与 requests 的 `reason_code`；2026-09-09 再增 sites/admin-sites 的 `quota_per_unit` 与 prices `source='sub2api'`）
> 契约权威：接口契约以 **Apifox 云端**为唯一事实来源；本文是实现设计稿，若字段与 Apifox 冲突，以 Apifox 为准

---

## 1. 背景与目标

主站（aiinfinite.online，NewAPI）把上游站点的模型转售给自己的用户。上游如果在后台**暗改倍率**（公示倍率不变、结算变贵），主站只看公示数据发现不了，表现为毛利缩水甚至亏本。

目标：对主站**每一条消费请求**，拿到费用、token 大小、缓存输出，与上游实际扣费对照，并按官方价格复算，回答三个问题：

1. 上游实扣是否符合它公示的分组倍率（→ 有没有暗改，红/绿）；
2. 主站计费是否与自己的分组倍率一致（→ 自己有没有配错）;
3. 主站实收 ÷ 上游实扣 是否等于两边分组倍率之比（→ 加价关系是否成立、是否亏本）。

UI 呈现：每条请求一行，**有问题红色、没问题绿色、无法核对灰色**。

## 2. 现状盘点

### 2.1 已具备的基础

| 已有 | 位置 | 对本功能的价值 |
|------|------|----------------|
| 渠道 ↔ 上游站点绑定 | `channel_upstream_bindings`（含 `upstream_base_url`、上游 `access_token`/登录态） | 主站某条请求走了哪个上游、用哪个凭据读上游日志 |
| 主站渠道明文 key 缓存 | `admin_channel_keys` | 渠道 key 属于上游哪个账号（核对可见性判断） |
| 上游公示倍率快照 | `sites.current_groups_json` + `snapshots`（来自 `/api/user/groups` + `/api/pricing`，含 model_ratio / group_ratio / completion_ratio） | 「上游期望成本」的分母——公示倍率 |
| 倍率变化检测 | `monitoring_service.detect_site` 的 `ratio_changed` | 只能发现公示变化，作为本功能的互补 |
| 调度 / 锁 / 推送 / 保留清理 | APScheduler、`core.state`、notification、`retention_service` | 定时核对、并发控制、红告警（P2）、历史清理先例 |

### 2.2 缺口

- 仓库从未接过任何 `/api/log*` 接口（主站管理日志、上游用户自查日志都没有）；
- 没有官方价格表（USD/1M tokens 口径）；
- 没有「请求级」任何数据，现有粒度最细到站点快照 diff。

## 3. 核心设计：三方对照

### 3.1 三组数据来源

| 数据 | 来源接口（只读） | 关键字段 |
|------|------------------|----------|
| 主站每条消费请求 | 主站 `GET /api/log/?type=2`（管理员） | `model_name`、`prompt_tokens`、`completion_tokens`、`quota`、`channel`、`other`（JSON：实际使用的 `model_ratio` / `group_ratio` / `completion_ratio` / 缓存 tokens，随版本有差异，做适配器） |
| 上游实际扣费 | 上游 `GET /api/log/self?type=2`（用渠道绑定的上游账号凭据） | `quota`（实扣）、`other`（上游当时实际用的倍率 = 暗改的**直接证据**） |
| 官方价格 | 新表 `official_model_prices`（内置种子 + UI 维护） | 输入 / 缓存命中 / 缓存写入 / 输出，USD per 1M tokens |

### 3.2 每条请求的四个计算值

```
official_usd        = (prompt×输入价 + cache_read×缓存读价 + cache_creation×缓存写价 + completion×输出价) / 1,000,000
                      （per_call 模型：official_usd = price_per_call_usd）

upstream_expected_usd = (prompt×model_ratio + cache_read×model_ratio×cache_ratio
                        + completion×model_ratio×completion_ratio) × group_ratio / quota_per_unit
                      ← 用「我方监控到的上游公示倍率快照」代入

upstream_actual_usd  = 上游日志 quota / quota_per_unit

main_usd             = 主站日志 quota / quota_per_unit
```

`quota_per_unit` 是 NewAPI 的 quota→美元基准（默认 500000，即 $1 = 500000 quota），设置项可改。

### 3.3 红 / 绿 / 灰判定规则（容差 T = tolerance_percent / 100，默认 1%）

| 顺序 | 判定 | 条件 | 结果 |
|------|------|------|------|
| 0 | 能否核对 | 无官方价 / 渠道没绑上游 / 绑定无凭据 / 没匹配到上游日志 / 关键倍率字段缺失 | ⚪ `unknown` 灰（缺什么记什么 reason code） |
| 1 | 暗改倍率 | `upstream_actual_usd` 偏离 `upstream_expected_usd` 超过 T，**或** 上游日志 `other` 里记录的 model_ratio / group_ratio ≠ 公示快照（超 T） | 🔴 `mismatch` + `upstream_ratio_drift` |
| 2 | 主站计费自洽 | 主站 `quota` 与主站日志自记倍率的重算值偏差超 T | 🔴 + `main_billing_inconsistent` |
| 3 | 加价倍率关系 | `main_usd ÷ upstream_actual_usd` 与 `主站分组倍率 ÷ 上游分组倍率` 的相对偏差超 T | 🔴 + `group_ratio_gap` |
| 4 | 亏本 | `main_usd − upstream_actual_usd < 0` | 🔴 + `negative_margin` |
| 5 | 全过 | 以上红条件都不成立 | 🟢 `ok` 绿 |

规则：reason_codes 列出**全部**命中的红因（一个请求可以又暗改又亏本）；任一命中即整行红。灰优先于红——核不了的数据绝不误报红。

## 4. 枚举与原因码

### 4.1 `status`（行级结果，红绿灰）

| 值 | 含义 |
|----|------|
| `ok` | 🟢 全部核对通过 |
| `mismatch` | 🔴 至少命中一个红因 |
| `unknown` | ⚪ 无法核对（灰），原因看 reason_codes |

### 4.2 `match_status`（上游日志匹配状态）

| 值 | 含义 |
|----|------|
| `matched` | 匹配到唯一上游日志 |
| `unmatched` | 有绑定有凭据，但时间窗内找不到 tokens 全等的上游日志 |
| `no_binding` | 主站渠道没有 `channel_upstream_bindings` 绑定 |
| `no_token` | 绑定存在但无上游账号凭据，读不了上游日志（UI 提示补绑定） |
| `no_log_api` | 上游平台不支持日志接口（P1 的 sub2api 上游即此状态） |

### 4.3 `reason_codes`（逐个讲解）

| 码 | 级别 | 讲解 |
|----|------|------|
| `upstream_ratio_drift` | 🔴 | 核心信号：上游实扣与「公示倍率算出的期望」不符，或上游日志自记倍率与公示快照不符——即后台暗改倍率的证据 |
| `main_billing_inconsistent` | 🔴 | 主站收的钱和它自己日志里记的倍率对不上，是主站自己的计费配置/版本问题，不是上游暗改 |
| `group_ratio_gap` | 🔴 | 主站与上游的实际费用比 ≠ 两边分组倍率之比，加价关系被破坏 |
| `negative_margin` | 🔴 | 这条请求主站收的比付给上游的还少，直接亏本 |
| `no_official_price` | ⚪ | 官方价格表里没有该模型，official_usd 算不出 |
| `no_upstream_log` | ⚪ | 时间窗内没匹配到上游日志（可能主站重试、时钟偏移过大、上游日志被清理） |
| `no_binding` / `no_token` | ⚪ | 渠道没绑上游 / 绑定无凭据，同 match_status |
| `ratio_field_missing` | ⚪ | 日志 `other` 里缺关键倍率字段（NewAPI 版本差异），期望成本算不准 |

## 5. 数据库设计（DDL 只进 `db/migrations.py`）

### 5.1 新表 `billing_request_checks`——一行 = 一条主站消费日志的核对结果

| 字段 | 类型 | 讲解 |
|------|------|------|
| `id` | BIGINT PK | 自增主键 |
| `admin_site_id` | INT | 主站 id（`admin_sites.id`），与 `main_log_id` 组成唯一键 |
| `channel_id` | INT | 主站渠道 id（主站日志里的 `channel`） |
| `channel_name` | VARCHAR(255) | 渠道名冗余存储，列表页免联查 |
| `upstream_site_id` | INT NULL | 经绑定映射到的上游监控站点 id（`sites.id`），映射不上为 NULL |
| `upstream_site_name` | VARCHAR(255) NULL | 同上冗余 |
| `main_log_id` | BIGINT | 主站日志 id，增量拉取的游标依据 |
| `request_at` | VARCHAR(40) | 请求时间（主站日志 `created_at`，ISO8601，复用 `core.time`） |
| `model_name` | VARCHAR(255) | 请求模型名，匹配与筛选主键之一 |
| `prompt_tokens` / `completion_tokens` | INT | 输入 / 输出 token 数（"大小"） |
| `cache_read_tokens` | INT NULL | 缓存命中（读）tokens，上游日志/other 取不到为 NULL（"缓存输出"） |
| `cache_creation_tokens` | INT NULL | 缓存写入 tokens，同上 |
| `main_quota` | BIGINT | 主站本条日志实际扣的 quota |
| `main_usd` | DOUBLE | `main_quota / quota_per_unit` |
| `main_model_ratio` / `main_group_ratio` / `main_completion_ratio` | DOUBLE NULL | 主站日志 `other` 里自记的倍率（判定 2 的输入） |
| `official_usd` | DOUBLE NULL | 官方成本，无官方价为 NULL |
| `official_input_usd_per_m` / `official_cached_input_usd_per_m` / `official_cache_write_usd_per_m` / `official_output_usd_per_m` | DOUBLE NULL | **核对时点用的官方单价快照**——价格表日后改价不影响历史记录复算 |
| `upstream_expected_usd` | DOUBLE NULL | 按公示倍率算的期望成本 |
| `upstream_actual_usd` | DOUBLE NULL | 上游实扣换算美元 |
| `upstream_log_id` | BIGINT NULL | 匹配到的上游日志 id |
| `upstream_model_ratio` / `upstream_group_ratio` / `upstream_completion_ratio` | DOUBLE NULL | 上游日志 `other` 自记倍率（暗改直接证据） |
| `published_model_ratio` / `published_group_ratio` / `published_completion_ratio` | DOUBLE NULL | 核对时点我方监控到的上游公示倍率快照 |
| `match_status` | VARCHAR(32) | 见 4.2，默认 `no_binding` |
| `status` | VARCHAR(32) | 见 4.1，默认 `unknown` |
| `reason_codes_json` | LONGTEXT | 原因码数组 JSON（可为多个） |
| `checked_at` | VARCHAR(40) | 本行最近一次核对时间（重跑会更新） |

唯一键 `uq_billing_check (admin_site_id, main_log_id)`；索引 `idx_billing_check_status (status, request_at)`、`idx_billing_check_site (admin_site_id, request_at)`。

### 5.2 新表 `official_model_prices`

| 字段 | 类型 | 讲解 |
|------|------|------|
| `model_name` | VARCHAR(255) PK | 模型名（按名 upsert；不做前缀通配，未收录即灰） |
| `quota_type` | VARCHAR(16) | `per_token` 按量 / `per_call` 按次 |
| `input_usd_per_m` | DOUBLE NULL | 官方输入价 USD / 1M tokens |
| `cached_input_usd_per_m` | DOUBLE NULL | 缓存命中输入价（如 Anthropic cache read、OpenAI cached input） |
| `cache_write_usd_per_m` | DOUBLE NULL | 缓存写入价（如 Anthropic cache creation 1.25x） |
| `output_usd_per_m` | DOUBLE NULL | 输出价 |
| `price_per_call_usd` | DOUBLE NULL | `per_call` 时每次调用官方价 |
| `source` | VARCHAR(16) | `builtin` 内置种子 / `manual` 用户维护（builtin 被改过即变 manual） |
| `updated_at` | VARCHAR(40) | 最近更新时间 |

内置种子随迁移插入常用模型（gpt-4o / gpt-4o-mini / claude 系列 / deepseek 等公开官方价），仅作起步值，UI 可改可删。

### 5.3 运行设置（存 `app_settings`，不新表）

| 键 | 默认 | 讲解 |
|----|------|------|
| `billing_audit_enabled` | `0` | 功能总开关（关了不跑定时、接口仍可查历史） |
| `billing_audit_interval_minutes` | `5` | 增量核对轮询间隔 |
| `billing_audit_tolerance_percent` | `1.0` | 偏差容差百分比（3.3 节的 T） |
| `billing_audit_match_window_seconds` | `300` | 主站↔上游日志时间匹配窗口 |
| `billing_audit_retention_days` | `30` | 核对记录保留天数，`0` = 永久 |
| `billing_audit_quota_per_unit` | `500000` | quota→美元基准（P1 全局一个值；按站点覆盖留 P2） |
| `billing_audit_push_on_red` | `0` | 出红是否走邮件/企微推送（P2 实现，P1 只存键） |

## 6. 新增接口设计（7 条路径 / 9 个方法端点，字段逐条讲解）

**通用约定**：路由挂 `/api` 前缀 + 统一 console 鉴权；响应信封 `{"success": bool, "data": ..., "message"?, "code"?}`；错误码沿用全局契约（401 `unauthorized`、404 记录不存在、422 `validation_error`、503 `database_busy`）。

### 6.1 `GET /api/billing-audit/overview` — 计费核对总览 KPI

查询参数：

| 参数 | 类型 | 必填 | 讲解 |
|------|------|------|------|
| `admin_site_id` | int | 否 | 按主站过滤 |
| `upstream_site_id` | int | 否 | 按上游站点过滤 |
| `channel_id` | int | 否 | 按主站渠道过滤 |
| `model` | string | 否 | 按模型名过滤（精确） |
| `start_at` / `end_at` | string | 否 | ISO8601 时间范围，按 `request_at` 过滤 |

响应 `data`：

| 字段 | 类型 | 讲解 |
|------|------|------|
| `total_count` | int | 核对记录总数（当前过滤条件下） |
| `ok_count` | int | 🟢 绿数量 |
| `mismatch_count` | int | 🔴 红数量 |
| `unknown_count` | int | ⚪ 灰数量 |
| `main_usd_total` | number | 主站实收合计 |
| `upstream_usd_total` | number | 上游实扣合计（仅 matched 参与合计） |
| `official_usd_total` | number | 官方成本合计（无官方价的记录不计入） |
| `margin_usd_total` | number | `main_usd_total − upstream_usd_total`，负数即整体亏钱 |
| `reason_breakdown` | array | `[{code, count}]` 红/灰原因分布——一眼看出主要是暗改、亏本还是缺价格/绑定 |
| `model_breakdown` | array | `[{model_name, total_count, mismatch_count}]` 按模型聚合，排序按 mismatch 降序 |

### 6.2 `GET /api/billing-audit/requests` — 明细列表（分页）

查询参数：6.1 的全部过滤参数之外，另有：

| 参数 | 类型 | 必填 | 讲解 |
|------|------|------|------|
| `page` | int | 否 | 默认 1 |
| `page_size` | int | 否 | 默认 20，上限 100 |
| `status` | string | 否 | `ok` / `mismatch` / `unknown`，单选 |

响应 `data`：`{total, page, page_size, items[]}`，`items` 每项字段：

| 字段 | 类型 | 讲解 |
|------|------|------|
| `id` | int | 核对记录 id（详情跳转用） |
| `admin_site_id` / `channel_id` / `channel_name` | int / int / string | 主站与渠道归属 |
| `upstream_site_id` / `upstream_site_name` | int / string·null | 上游归属，没绑上游为 null |
| `main_log_id` | int | 主站日志 id |
| `request_at` | string | 请求时间 |
| `model_name` | string | 模型名 |
| `prompt_tokens` / `completion_tokens` | int | 输入 / 输出 tokens |
| `cache_read_tokens` / `cache_creation_tokens` | int·null | 缓存读 / 写 tokens |
| `main_quota` / `main_usd` | int / number | 主站扣费 quota 与美元 |
| `main_model_ratio` / `main_group_ratio` / `main_completion_ratio` | number·null | 主站自记倍率 |
| `official_usd` | number·null | 官方成本 |
| `official_input_usd_per_m` 等四价 | number·null | 核对时点官方单价快照 |
| `upstream_expected_usd` / `upstream_actual_usd` | number·null | 上游期望 / 实扣 |
| `upstream_log_id` | int·null | 匹配到的上游日志 id |
| `upstream_model_ratio` / `upstream_group_ratio` / `upstream_completion_ratio` | number·null | 上游自记倍率 |
| `published_model_ratio` / `published_group_ratio` / `published_completion_ratio` | number·null | 公示倍率快照 |
| `match_status` | string | 见 4.2 |
| `status` | string | `ok` / `mismatch` / `unknown`（红绿灰渲染依据） |
| `reason_codes` | string[] | 原因码数组，见 4.3 |
| `checked_at` | string | 核对时间 |

### 6.3 `GET /api/billing-audit/requests/{check_id}` — 单条详情

路径参数：`check_id`（int，必填）。响应 = 6.2 的单条字段，额外多两个原始证据字段：

| 字段 | 类型 | 讲解 |
|------|------|------|
| `main_other_json` | object·null | 主站日志 `other` 字段解析结果（原始倍率证据） |
| `upstream_other_json` | object·null | 上游日志 `other` 字段解析结果 |

错误：404 记录不存在。

### 6.4 `GET /api/billing-audit/settings` / `PUT /api/billing-audit/settings` — 设置读写

GET 响应与 PUT 请求体同构：

| 字段 | 类型 | 讲解 |
|------|------|------|
| `enabled` | bool | 总开关 |
| `interval_minutes` | int | 轮询间隔（5–1440，越界 422） |
| `tolerance_percent` | number | 容差（0–50） |
| `match_window_seconds` | int | 时间匹配窗口（60–3600） |
| `retention_days` | int | 保留天数（0 = 永久） |
| `quota_per_unit` | int | quota→美元基准 |
| `push_on_red` | bool | 红告警开关（P1 只存不生效，界面标「即将支持」） |

### 6.5 `GET /api/billing-audit/prices` / `PUT /api/billing-audit/prices` — 官方价格表

GET 响应：`{items[]}`，每项字段：

| 字段 | 类型 | 讲解 |
|------|------|------|
| `model_name` | string | 模型名（唯一） |
| `quota_type` | string | `per_token` / `per_call` |
| `input_usd_per_m` / `cached_input_usd_per_m` / `cache_write_usd_per_m` / `output_usd_per_m` | number·null | 四档官方单价，`per_call` 时可全空 |
| `price_per_call_usd` | number·null | 按次价 |
| `source` | string | `builtin` / `manual` |
| `updated_at` | string | 最近更新时间 |

PUT 请求体 = 上述除 `source` / `updated_at` 外的字段，按 `model_name` upsert；`source` 自动变 `manual`。响应 `data.item` 为落库后的行。

### 6.6 `DELETE /api/billing-audit/prices/{model_name}` — 删除官方价格

路径参数 `model_name`（string）。删除后该模型新核对记 `no_official_price` 灰；历史记录里的单价快照不受影响。

### 6.7 `POST /api/billing-audit/run` — 手动立即核对

请求体（可选）：`{"admin_site_id": int}`——只传该主站；不传则全部主站。响应 `data`：`{"triggered": true}`。幂等：已在跑时重复触发直接返回 true，不排队不重复拉。

## 7. 上游日志匹配算法

1. 渠道 → `channel_upstream_bindings` 查 `upstream_base_url` 与上游凭据 → `sites` 表按 URL 匹配监控站点（拿公示倍率快照）；
2. 拉上游该账号 `type=2` 日志，候选条件：`model_name` 相同 + `prompt_tokens` / `completion_tokens` 相同 + 缓存 tokens 相等（可得时）+ `|上游时间 − 主站时间| ≤ match_window_seconds`；
3. 取时间差最小的一条；一条上游日志只允许配一条主站记录（按 `upstream_log_id` 占用去重）；
4. 匹配失败不报红，按 4.2 归灰。

## 8. 后端模块划分（全部新文件，不膨胀存量）

| 文件 | 职责 |
|------|------|
| `integrations/newapi_logs.py` | 主站 admin 日志、上游 self 日志的读取客户端 + `other` 字段版本适配器 |
| `repositories/billing_audit.py` | 两张新表的 SQL |
| `services/billing_audit_service.py` | 增量拉取、匹配、判定、汇总编排 |
| `workers/billing_audit_worker.py` | 挂现有 APScheduler 的周期任务（`max_instances=1` + `coalesce`） |
| `api/routers/billing_audit.py` + `api/schemas/billing_audit.py` | 本文第 6 节 7 个接口 |

依赖方向：`routers → services → repositories / integrations → core / db`；锁从 `core.state` 注册取用。

## 9. 前端设计（Vue 3）

| 产物 | 内容 |
|------|------|
| `lib/api/billingAudit.ts` | 7 个接口的 api 域文件（对应后端 router）；类型进 `lib/types.ts` |
| `pages/BillingAuditPage.vue` | 新页面「计费核对」，`router/index.ts` 懒加载注册 + `AppShell.vue` 导航数组加项 |
| 页面布局 | 顶部 KPI 卡（核对数 / 红 / 绿 / 灰 / 毛利合计）→ 筛选条（主站 / 渠道 / 模型 / 状态 / 时间范围）→ 高密度表格：时间、模型、tokens（含缓存）、官方成本、上游实扣、主站实收、状态胶囊（红 `mismatch` / 绿 `ok` / 灰 `unknown`，用现有状态胶囊语义类）→ 行点击抽屉展示双侧倍率对比与复算公式 |
| 弹窗 | 官方价格表管理（表格 + 行内编辑）；设置（7. 节字段表单） |

状态色只用语义 token（暖纸+浓墨既有红/绿/灰胶囊），不写死 hex。

## 10. 调度与数据量控制

- 每 `interval_minutes` 一轮：按主站增量拉 `type=2` 日志（游标 = 上次最大 `main_log_id`，每轮页数上限防风暴）→ 落新行 → 对未核对行做匹配与判定 → 更新行；
- 上游日志按需拉取（仅对有绑定有凭据的渠道），不轮询全量；
- 保留清理复用 retention 先例，按 `retention_days` 删旧行；
- 手动 `POST /run` 与定时共用同一把主站级锁，天然互斥。

## 11. 边界与风险

- **上游日志可见性是硬前提**：渠道 key 所属上游账号 ≠ 绑定凭据账号时读不到对应日志，归 `no_token` 灰并提示补绑定——绝不误报红；
- **NewAPI 版本差异**：`other` 字段键名不一（`cache_tokens` / `cache_creation_tokens`…），适配器逐键兼容，缺关键键归 `ratio_field_missing` 灰；
- **按次计费模型**（model_price）走 `per_call` 分支；
- **时钟偏移 / 主站重试**：时间窗可配（默认 300s），匹配不上归灰；
- **上游改价与历史**：单价与公示倍率都做快照落行，历史判定不受后续改价影响；
- **缓存 tokens 口径（已知限制）**：公式假设主站/上游日志的 `prompt_tokens` 不含缓存命中 tokens；部分 NewAPI 版本的 `prompt_tokens` 已含缓存子集，会把缓存双算导致 official_usd/期望成本偏大。验收时用一条真实缓存命中日志核对口径，必要时在适配器里做版本分支；
- **跨轮上游日志去重**：同轮内用内存 `usedLogIds`，跨轮以库中已占用的 `upstream_log_id` 排除；主站重试产生的两条上游日志属合法双消费，不受影响；
- **只读安全**：所有上游/主站调用均为 GET 日志读接口，不碰写接口；
- **密钥纪律**：渠道 key 不落本功能表（复用 `admin_channel_keys`），UI 不展示明文凭据。

## 12. 分期计划

| 期 | 内容 |
|----|------|
| **P1** | 两张表 + `newapi_logs` 集成 + 核对 service/worker + 7 个接口 + 计费核对页面（红绿灰 + 详情抽屉 + 价格表 + 设置）；覆盖 NewAPI 主站 + NewAPI 上游 |
| **P2** | sub2api 主站/上游适配；`push_on_red` 邮件/企微告警；按上游 / 按模型聚合报表（"哪个上游在暗改"排名）；`quota_per_unit` 按站点覆盖 |

实施顺序（确认后）：master 拉 `billing-audit` 分支 → migrations → integration → repository → service/worker → router/schemas → 前端 → 构建起 8020 验证环境交用户验收。

## 13. 待用户确认项

1. 去 Apifox 审阅 7 个接口，确认后开工；
2. 官方价格表「内置种子 + 手动维护」是否够用；
3. 红色告警推送放 P2、P1 先页面看红，是否接受；
4. 容差默认 ±1% 是否合适。
