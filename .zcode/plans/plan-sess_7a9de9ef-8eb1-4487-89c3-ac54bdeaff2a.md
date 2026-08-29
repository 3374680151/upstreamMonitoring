# AGENTS.md 收敛为单一权威文件

目标：仓库只留根目录一份 `AGENTS.md`；按之前确认的顺序（1→3→5→4→8，附带 2/6/7）修正全部矛盾点；`backend/AGENTS.md`、`apps/web/AGENTS.md` 的硬规则压缩合并进根文件后删除。**不改任何代码。**

## 0. 前置
- 从 master 新建分支 `chore/agents-md-consolidation`（当前工作区未提交的 Git 工作流章节一并带入），本次改动以一个 commit 落在分支上，交你验证后再合入 master + push。

## 1. UI 权威统一（问题 1+2）
- 根 AGENTS.md「UI」章节改为真实主题「暖纸+浓墨」：页底 `#f4f1ea`、品牌绿 `#2c8a5a`、白卡片 `#ffffff`、暗色页底 `#0d1011`；唯一权威 = 本文件 + `apps/web/src/styles/tokens.css`；保留宋体 `font-serif`（仍有效）与「双主题浏览器验证」要求。
- 强制规则 2 从「必须遵循 priceai-ui skill」改为指向上述权威。
- 删除工作区 `.agents/skills/priceai-ui/`（被 gitignore、色值过时；用户级 `~/.agents` 那份不属本仓库、不动，仅提示它也是旧色值）。
- 同步修引用：`README.md` 结构树与 UI 段落（删 skill 行、改新色值）；`docs/product.md:115` skill 引用改为指向根 AGENTS.md（`design/priceai-style.md` 保留为调研归档）。

## 2. 结构树修正（问题 3）
- 重画「仓库结构」树：新增 `extensions/upstream-session-bridge/`、`README.md`、`requirements.txt`；删除 `backend/legacy_runtime.py` 行、两份子 AGENTS.md 行、`.agents/skills` 行。
- 删除空目录 `backend/domain/`（git 未跟踪、全仓库 0 引用）。

## 3. 规则 6 改写（问题 5）
- 改为明确语义：**基础设施类**（连接池/调度/HTTP 客户端/TTL 缓存/.env 解析）用成熟三方库；**业务逻辑**优先标准库；白名单外新增必须先登记 AGENTS.md。

## 4. 主站识别改为按 URL（问题 4）
- 删掉「主站 id=2」硬编码：改为「操作主站前按 URL 含 `aiinfinite.online` 确认目标；数据库自增 id 会随重建漂移，禁止作为身份依据」。保留 fake 数据不得覆盖真实主站的禁令。

## 5. `.env` 键清单补全（问题 8）
- 补上 `TZ`、`SEED_DEMO`、`DB_CONNECT_TIMEOUT / DB_READ_TIMEOUT / DB_WRITE_TIMEOUT`。

## 6. 合并子文件内容并删除（核心步骤）
- 根文件新增两章（替换现在指向子文件的占位段）：
  - **后端规范（FastAPI）**：分层职责（router=接口壳 / service=业务 / repository=SQL / integrations=第三方 HTTP / core.state=锁与单例唯一来源 / normalize=纯函数）；新 endpoint 标准流程（压缩版）；鉴权走 `protected` 字典、401 契约 `code:"unauthorized"` 与前端 `console-unauthorized` 事件联动；响应信封 `{success,data,message,code}` + 三类异常 handler；DB 约定（占位符 `?` 经 `_q()` 转义、助手函数、表结构只改 `db/migrations.py` 的 `init_db()`）；锁清单（DB_LOCK / MODEL_CACHE_LOCK / MAIN_CHANNEL_KEY_REQUEST_LOCK / 三套浏览器会话锁不可混用）；integrations（httpx 共享 Client trust_env=False + curl_cffi 回退）；Workers（max_instances=1+coalesce、资源统一 lifespan 起停）；配置新增三步；技术债例外（monitoring.sync_sites / discovery_import 勿模仿）。
  - **前端规范（Vue 3）**：技术栈红线（禁 React/Pinia、图标只 lucide-vue-next）；vue-tsc 坑（build 用 `--noEmit -p tsconfig.json`，禁改回 `vue-tsc -b`，会挂死）；登录非独立路由、401 事件驱动；类型唯一来源 `lib/types.ts`、透传用 `unknown`；分层边界（ui/ 禁依赖 composables 与 lib/*、业务组件不互相 import）；api client 约定（按域对应后端 router、`client.ts` 是 token 唯一入口、页面只 `import { api }`）；金额只用 `format.ts` 的 `usd()`；样式只消费语义工具类不写死 hex；删除文件前先全局搜引用。
  - 丢弃：两份文件里的目录树（根结构树已有）、6 条记忆链接（根文件已有自包含正文）、React 时代残留描述。
- `git rm backend/AGENTS.md apps/web/AGENTS.md`。

## 7. 去重 + Git 工作流收尾（问题 6+7）
- 「不要做」删除与强制规则/新章节重复的条目（密钥、依赖、测试、两类站点、master 工作流 5 处），只保留独有禁止项。
- Git 工作流补边界：代码功能/修复必须走分支；纯文档微调可直接在 master；一次一个功能分支不变。
- 删除已完全合入 master 的残留分支 `feat/channel-auto-demotion`。

## 8. 验证与交付
- grep 校验残留：`backend/AGENTS.md`、`apps/web/AGENTS.md`、`priceai-ui`、`#45bf78`、`#f9f9f9` 全仓库（除 design/ 调研归档与 docs/superpowers/ 历史计划）应为 0 命中。
- 分支上提交（`docs: AGENTS.md 收敛为唯一权威，合并前后端规范并修正矛盾`），交你验证。
- 你验证通过后：合入本地 master → `git push origin master`。

**明确不做**：不动 `docs/superpowers/` 历史计划归档；不动用户级 skill；不改代码、不新增测试。