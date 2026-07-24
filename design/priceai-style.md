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
