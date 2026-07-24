# Upstream 产品说明

## 一句话

自建的 AI 上游 / 中转控制面：管渠道、看稳定性、查全量请求（含失败）。

## 背景问题（来自真实踩坑）

使用第三方中转（如 tokentuan）时：

- 客户端（Hermes / OpenClaw）频繁收到 `HTTP 503 Service temporarily unavailable`
- 站点 Usage 页往往**只展示成功计费请求**，失败不可见
- 无法区分：本机配置错误 vs 上游过载 vs 模型池（如 `build-free`）容量问题

Upstream 要成为「自己可控的一层」：观测与路由策略掌握在自己手里。

## 目标用户

- 需要多上游聚合的个人/小团队
- 需要给 OpenClaw / Hermes / 自研客户端提供稳定 OpenAI-compatible 入口的人

## MVP 范围

### P0

- [ ] 控制台壳 + PriceAI 风格设计系统
- [ ] 渠道列表 / 渠道详情（mock 数据可跑）
- [ ] 请求日志模型：status、latency、error、request_id、model、stream
- [ ] 健康探测任务（定时 ping `/v1/models` + 轻量 chat）

### P1

- [ ] OpenAI-compatible 转发代理
- [ ] 多上游 failover / 重试策略
- [ ] API Key 与额度
- [ ] 成功率、延迟分位图

### P2

- [ ] 倍率/成本看板
- [ ] 告警（连续 503、可用率跌破阈值）
- [ ] 多租户

## 非目标（当前不做）

- 克隆 PriceAI 全站比价爬虫
- 自动注册/支付灰色产业链
- 攻击第三方上游

## 成功标准

1. 任意 503 都能在「请求日志」里按 request_id 查到
2. 控制台第一眼能看到各上游可用率与最近失败原因
3. UI 观感与 PriceAI 同族：冷静、密、翠绿点缀
