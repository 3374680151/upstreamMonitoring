# 计费核对 Dashboard 聚合 — 实现与操作记录

> 日期：2026-09-08
> 分支：`billing-audit-dashboard` → 合入 master（merge commit `d194a2a`，已推送 origin）
> 契约：Apifox 项目 `8772928`「计费核对 → billing-audit」目录，`GET /overview` 与 `GET /requests` 增量推送（endpointUpdated: 2）
> 前置文档：[billing-audit-方案.md](./billing-audit-方案.md)（P1 三方对照，本篇是其 dashboard 聚合层增量）

---

## 1. 需求来源

用户对计费核对页提出三类诉求（对话确认）：

1. 页面信息密度审视：全局统计之下缺「按维度聚合」的中间层——数据（含后端已返回但前端未渲染的 `model_breakdown` / `reason_breakdown`）应该展示在哪里；
2. 「每个渠道整体的」统计：按上游站点聚合核对结果，一眼锁定哪个上游在暗改；
3. 「5 分钟的那种」：核对默认 5 分钟一轮，要一轮一根柱的趋势带。

确认方案为三层漏斗 + 明细：KPI → 趋势带（时间维度）→ 渠道汇总（上游维度）→ 模型/原因分布 → 单条明细（证据链留在详情弹窗）。

## 2. 接口契约变更（先设计后实现）

| 端点 | 变更 | 说明 |
|------|------|------|
| `GET /api/billing-audit/overview` | 响应新增 `time_buckets[]` | `{bucket_start_at, total_count, ok_count, mismatch_count, unknown_count, margin_usd}`，只返回非空桶、时间升序 |
| `GET /api/billing-audit/overview` | 响应新增 `upstream_breakdown[]` | 按 `upstream_site_id` 聚合（mismatch 降序，≤20 条），含 `top_reason_code`（红灰混合口径） |
| `GET /api/billing-audit/overview` | 参数新增 `bucket_minutes`（1–1440，默认 5）与 `reason_code` | 前端按 range 自适应：1h→5 / 24h→15 / 7d→120 / 30d→480 / 全部→1440 |
| `GET /api/billing-audit/requests` | 参数新增 `reason_code` | 全部为纯新增字段/参数，老字段不动 |

推送过程备忘：本机代理劫持 `api.apifox.com`（UDP 53 返回 fake-ip 198.18.x），采用 DoH over TLS（223.5.5.5，SNI `dns.alidns.com`）解析真实 IP + SNIConn 直连推送；`input` 必须是**字符串化的 OpenAPI**（嵌套对象会 422）；OpenAPI 3.0 的可空字段用 `nullable: true` 而非 `oneOf`+null。云端导出回读接口对本 token 返回 302，未做导出复核，以 import 计数器为准。

## 3. 实现清单

**后端**（聚合拆新模块，遵守 500 行红线）：

- `repositories/billing_audit_overview.py`（新）：`overviewBillingChecks`（KPI / 模型 / 上游 / 时间桶四组聚合）、`applyReasonCodeCondition`（`reason_codes_json` LIKE 过滤：双引号边界 + 白名单 `alnum+_` + `_` 转义）、`_topReasonCode`。时间桶 SQL：`FLOOR(UNIX_TIMESTAMP(request_at)/N)*N`（`request_at` 是带 `+08:00` 的 ISO 串，MySQL ≥8.0.19 显式偏移解析与 Python `datetime.fromtimestamp` 闭环一致），epoch 由 Python 按 `APP_TIMEZONE` 格式化；`DESC + LIMIT 400` 保最新桶再反转，跳过脏数据 NULL 桶。
- `services/billing_audit_overview_service.py`（新）：`billingAuditOverviewPayload`；时间归一复用 `billing_audit_service.normalizeTimeBound`（原 `_normalizeTimeBound` 转公共，services 互调只走顶层公共函数）。
- `repositories/billing_audit.py` / `services/billing_audit_service.py`：移除搬走的代码，`listBillingChecksPayload` 加 `reasonCode`。
- `api/routers/billing_audit.py`：新参数接驳，`bucket_minutes` 用 `Query(5, ge=1, le=1440)`，ValueError → 422。

**前端**（全部走语义 token，无图表库依赖）：

- `BillingAuditTrend.vue`（新）：纯 CSS 堆叠柱趋势带，每桶一轮；红桶 ▲ 标记 + 图例文字（颜色非唯一载体）；空桶补齐保证时间轴连续，超 96 桶抽稀；点柱锁定时段。
- `BillingUpstreamBreakdown.vue`（新）：渠道汇总表（红率横条、主要原因、毛利负值标红），`upstream_site_id: null` 行禁点。
- `BillingBreakdownBars.vue`（新）：问题模型 TOP5 + 红灰原因分布横条（数值直接标注），点击走服务端 `model` / `reason_code` 过滤。
- `BillingChecksTable.vue`：新增「倍率对照」列（上游实际 / 公示模型倍率，`upstream_ratio_drift` 行标红，悬停展开三组 + 主站倍率证据）。
- `BillingAuditPage.vue`：三层漏斗接线；`pinnedBucket` 锁定时段（记桶宽快照，切 range 不错位）；KPI 异常/灰卡主因按红灰码集合分取（`GRAY_REASON_CODES`）；单 watch 驱动全部筛选（防双请求）；`end_at` 减 1 秒防闭区间压线跨桶。

## 4. 提交（3 轮审计循环）

| 轮次 | 结论 | 修复 |
|------|------|------|
| R1 | FAIL（4×P1） | 红 KPI 卡硬编码非法 reason_code 必 422；`filterModel` 死分支 + keyword 冒充服务端过滤（改走 `model` 参数 + 芯片）；`upstream_site_id:null` 行点击反成清除筛选；500 行红线（拆新模块）。顺修 P2：桶 `DESC+LIMIT` 保最新、LIKE `_` 转义、NULL 桶跳过、锁定提示统一 ISO、`end_at` −1s、EmptyState 走 ui 桶出口、「主要红因」→「主要原因」 |
| R2 | FAIL（1×P1） | `selectUpstream` 改 watched ref 又手动 `fetchAll` 双请求 → 单 watch 统一驱动全部筛选；`normalizeTimeBound` 双实现去重；红卡主因对称取红因集合；边界死按钮消灭；锁定桶宽快照 |
| R3 | **PASS**（P0/P1/P2 = 0） | P3 顺修：`billing_audit.py` 死导入 `json` 移除、误导注释修正 |

提交：`30ffa13 feat(billing-audit): 计费核对 dashboard 聚合——趋势带/渠道汇总/模型与原因分布/倍率对照列`（12 文件，+855/−133）。提交时只圈定本分支相关文件，未带入工作区其他未跟踪杂物（`docs/audit/*`、`docs/主站监控-*.md`、`.zcode/plans/`）。

## 5. 验证与合并

- 验证环境：8040 端口（`SCHEDULER_ENABLED=0`），`/healthz` ok、overview/requests 新字段实测正确（100 行 → 9 个 5 分钟桶 + 7 行上游聚合）、非法 `reason_code` 422。
- 用户走查主流程（浅色 + 暗色）后确认，执行合入：`git checkout master && git merge --no-ff billing-audit-dashboard && git branch -d billing-audit-dashboard && git push origin master` → master `f7ba225..d194a2a` 推送成功。

## 6. 遗留与后续项

1. **官方价定时同步（未实现）**：方案已与用户对齐——挂现有调度器每日一次，读 `admin_site_sync_state.channels_json` 里 sub2api 主站的渠道 `model_pricing`（per-token 美元价 ×1e6 转每 1M 价）upsert 进 `official_model_prices`，不覆盖 `source='manual'` 行；`source` 枚举是否细分 `sub2api`（契约小变更，需再推 Apifox）待用户选 A/B。
2. **键盘可达性（P3）**：趋势柱/渠道行点击与存量表格行点击一致（div/tr @click），未加 tabindex/role；`BillingBreakdownBars` 已用真 `<button>`，后续统一补。
3. **锁定状态下重点同一根柱**：overview 刷新后会产生一次参数相同的幂等重复请求（低频、无数据错误），按审计建议不修。
4. **1440 分钟桶对齐**：桶沿按 epoch 对齐，「全部」档日桶起点是本地 08:00 而非自然日 00:00，已在代码注释说明。
