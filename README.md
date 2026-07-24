# Upstream

AI 上游 / 中转控制面脚手架。UI 风格对齐 [PriceAI 中转详情页](https://priceai.cc/api-transit/ai-rtoc-cc)。

## 快速开始

```bash
cd ~/Desktop/upstream/apps/web
npm install
npm run dev
```

浏览器打开终端提示的本地地址（默认 `http://localhost:5173`）。

## 仓库结构

```text
upstream/
  AGENTS.md                      # Agent 全局规则（必读）
  docs/product.md                # 产品范围
  design/priceai-style.md        # UI 调研
  .agents/skills/priceai-ui/     # UI skill
  apps/web                       # React 控制台
  apps/api                       # 后端占位
```

## Agent 协作

1. 读 `AGENTS.md`
2. UI 任务加载 skill：`.agents/skills/priceai-ui/SKILL.md`
3. 全局 skill 副本：`~/.agents/skills/priceai-ui/`（与仓库同步）

## 当前进度

- [x] 项目脚手架
- [x] PriceAI 风格 token + skill + AGENTS 规则
- [x] Web 壳与渠道详情 mock 页
- [ ] 真实 API / 代理 / 日志入库
