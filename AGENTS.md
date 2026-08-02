# Upstream — Agent 全局规则

> 路径：`/Users/wang/Desktop/upstream`  
> 自 `Documents/上游配置/upstream-ratio-watch` 迁移；功能以 `app.py` 为准，UI 以 PriceAI 风格 React 为准。

---

## 项目定位

轻量级**上游 AI 分组倍率监控面板**：

- 监控 NewAPI / sub2api 等上游站点的分组、倍率、描述等变化
- 定时采集 + diff + 变化记录 + 邮件/企业微信推送
- 适合少量上游日常盯盘

| 层 | 实现 |
|----|------|
| 后端 | 根目录 `app.py`（stdlib HTTP + MySQL，驱动 PyMySQL） |
| 前端 | `apps/web`（React + Vite + Tailwind + priceai-ui） |
| 数据 | MySQL 数据库（连接走 `.env`，见 `DB_*`） |
| 部署 | `python3 app.py` / Docker Compose（自带 mysql:8.4） |

---

## 强制规则

1. 对用户默认**简体中文**
2. UI 必须遵循 skill **`priceai-ui`**（`.agents/skills/priceai-ui/SKILL.md`）
3. **兼容现有 MySQL 数据**，禁止未确认清空用户数据；`data/` 里的旧 SQLite 与备份整体 gitignore
4. 密钥（DB 密码、token、webhook）只放本地 `.env`，勿写入源码 / git / 对话明文
5. 推送通道：邮件 + 企业微信；不主动复活 QQ 推送
6. 后端依赖收敛：**仅允许 PyMySQL 一个第三方库**（数据库驱动），其余走标准库；前端可在 `apps/web` 使用 npm 依赖
7. 改 API 契约时同步改 `apps/web/src/lib/api.ts` 与页面

---

## UI（PriceAI）

- 页底 `#f9f9f9`，白卡片，品牌绿 `#45bf78`
- 标题可用宋体 `font-serif`；正文无衬线
- 状态胶囊 + KPI + 高密度表
- token：`apps/web/src/styles/tokens.css`
- 暗色：`html[data-theme=dark]`

---

## 功能边界（必须保留）

1. 站点 CRUD（NewAPI / sub2api）
2. 定时检测 + 手动检测
3. 快照 + 分组/倍率 diff（含模型上下架 `model_added_to_group` / `model_removed_from_group`）
4. 变化列表
5. 邮件 + 企业微信（含测试发送）
6. 总览 KPI / 站点详情 / 分组倍率弹窗 / 模型健康
7. 账户额度：NewAPI `/api/user/self`、sub2api `/api/v1/auth/me`（站点详情页按需查询）

---

## 本地验证

```bash
# 后端
python3 app.py

# 前端开发
cd apps/web && npm run dev

# 生产 UI 构建后由 app.py 托管
cd apps/web && npm run build && cd ../.. && python3 app.py
```

至少验证：`/api/overview`、`/api/sites` 可访问；页面可添加/编辑/检测站点；推送页可保存配置。

---

## 不要做

- 不要丢掉用户 `data/app.db`
- 不要改成深色 Linear 风却声称 PriceAI
- 不要为「好看」删监控字段与检测能力
- 不要在未请求时引入重型 Python 框架（Django/FastAPI 等）替换整个后端
