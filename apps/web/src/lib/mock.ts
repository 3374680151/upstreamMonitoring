export type Channel = {
  id: string;
  name: string;
  baseUrl: string;
  stack: string;
  status: "available" | "degraded" | "down";
  verified: boolean;
  updatedAt: string;
  description: string;
  gptRate: string;
  claudeRate: string;
  availability: string;
  samples: number;
  lastCheck: string;
  groups: Array<{
    name: string;
    rate: string;
    models: number;
    cacheHit: string;
  }>;
  recentLogs: Array<{
    id: string;
    time: string;
    model: string;
    status: number;
    latencyMs: number;
    error?: string;
  }>;
};

export const channels: Channel[] = [
  {
    id: "demo-rtoc",
    name: "Demo Upstream",
    baseUrl: "https://example-upstream.local/v1",
    stack: "OpenAI Compatible",
    status: "degraded",
    verified: true,
    updatedAt: "2026-07-25",
    description:
      "脚手架演示渠道。真实代理接入后，将展示上游倍率、探测可用率与全量请求日志（含 503 失败）。",
    gptRate: "0.040x",
    claudeRate: "0.22x",
    availability: "86.4%",
    samples: 972,
    lastCheck: "2026-07-25 00:00",
    groups: [
      { name: "GPT", rate: "0.040x", models: 5, cacheHit: "91.5%" },
      { name: "GPT Pro", rate: "0.20x", models: 6, cacheHit: "88.7%" },
      { name: "Claude", rate: "0.22x", models: 4, cacheHit: "84.1%" },
    ],
    recentLogs: [
      {
        id: "req_7a49fc47",
        time: "01:42:04",
        model: "grok-4.5",
        status: 200,
        latencyMs: 1941,
      },
      {
        id: "req_07d7d802",
        time: "01:36:32",
        model: "grok-4.5",
        status: 503,
        latencyMs: 1941,
        error: "Service temporarily unavailable",
      },
      {
        id: "req_38531036",
        time: "01:36:30",
        model: "grok-4.5",
        status: 503,
        latencyMs: 2612,
        error: "api_error",
      },
      {
        id: "req_60c520d4",
        time: "01:37:06",
        model: "grok-4.5",
        status: 200,
        latencyMs: 1320,
      },
    ],
  },
];

export function getChannel(id: string) {
  return channels.find((c) => c.id === id) ?? channels[0];
}
