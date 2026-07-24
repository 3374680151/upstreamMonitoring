import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { StatCard } from "@/components/StatCard";
import { Panel } from "@/components/Panel";
import { Badge } from "@/components/Badge";
import { channels } from "@/lib/mock";

export function OverviewPage() {
  const totalSamples = channels.reduce((s, c) => s + c.samples, 0);
  const failish = channels.filter((c) => c.status !== "available").length;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold text-[var(--color-text-primary)] md:text-3xl">
          上游控制台
        </h1>
        <p className="mt-1 max-w-3xl text-sm text-[var(--color-text-muted)]">
          管理 AI 中转上游、观测可用率，并记录包含 503 在内的失败请求。UI
          风格对齐 PriceAI 中转详情页。
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="渠道数" value={channels.length} hint="当前 mock 数据" />
        <StatCard label="监测样本" value={totalSamples} hint="累计探测" />
        <StatCard
          label="需关注"
          value={failish}
          hint="降级 / 不可用"
          accent={failish > 0}
        />
        <StatCard label="日志策略" value="全量" hint="成功 + 失败均入库" />
      </div>

      <Panel
        title="渠道速览"
        subtitle="点击进入详情（PriceAI 风格 KPI + 日志表）"
      >
        <div className="space-y-3">
          {channels.map((c) => (
            <Link
              key={c.id}
              to={`/channels/${c.id}`}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-panel-soft)] px-4 py-3 transition hover:bg-[var(--color-surface-hover)]"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-bold text-[var(--color-text-primary)]">
                    {c.name}
                  </span>
                  <Badge
                    tone={
                      c.status === "available"
                        ? "success"
                        : c.status === "degraded"
                          ? "warning"
                          : "danger"
                    }
                    dot
                  >
                    {c.status === "available"
                      ? "可用"
                      : c.status === "degraded"
                        ? "降级"
                        : "不可用"}
                  </Badge>
                  {c.verified ? <Badge tone="info">已核验</Badge> : null}
                </div>
                <div className="mt-1 text-xs text-[var(--color-text-muted)]">
                  可用率 {c.availability} · 样本 {c.samples} · 最近检查{" "}
                  {c.lastCheck}
                </div>
              </div>
              <span className="inline-flex items-center gap-1 text-sm font-semibold text-[var(--color-text-muted)]">
                查看详情 <ArrowRight size={14} />
              </span>
            </Link>
          ))}
        </div>
      </Panel>
    </div>
  );
}
