# 产品说明

## 定位

Upstream 是上游 AI 中转分组倍率监控控制台：盯 NewAPI / sub2api 的分组与倍率变化，并推送告警。

## 已迁移功能（来自 upstream-ratio-watch）

| 模块 | 说明 |
|------|------|
| 站点管理 | 添加/编辑/删除；NewAPI、sub2api |
| NewAPI 采集 | 公开 `/api/user/groups`；可选系统访问令牌增强 |
| sub2api 采集 | 账号密码或导入 auth_token/refresh_token |
| 调度 | 按站点 interval 定时检测；可手动检测 |
| Diff | 倍率/分组增删/描述/专属/订阅/RPM 等 |
| 模型健康 | 站点模型接口缓存与弹窗展示 |
| 推送 | SMTP 邮件 + 企业微信 Webhook + 测试发送 |
| 数据 | SQLite `data/app.db` 全量兼容 |

## 配置入口（完整）

所有业务配置在 UI 中完成并写入 DB：

1. **站点表单**：名称、平台、Base URL、间隔、启用、认证字段（密码/token 编辑可留空表示不改）
2. **消息推送页**：企业微信开关+Webhook；SMTP 全字段+SSL+测试
3. **运行环境**：`HOST` / `PORT` / `APP_TIMEZONE`

## UI 原则

PriceAI 风格：见 skill `priceai-ui` 与 `design/priceai-style.md`。
