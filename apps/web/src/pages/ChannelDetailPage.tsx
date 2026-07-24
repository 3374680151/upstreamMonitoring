import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Badge } from "@/components/Badge";
import { StatCard } from "@/components/StatCard";
import { Panel } from "@/components/Panel";
import { getChannel } from "@/lib/mock";

export function ChannelDetailPage() {
  const { id } = useParams();
  const channel = getChannel(id ?? "");

  return (
    <div className="space-y-6">
      <Link
        to="/channels"
        className="inline-flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text-muted)] transition hover:text-[var(--color-text-primary)]"
      >
        <ArrowLeft size={14} /> 返回上游渠道列表
      </Link>

      <div className="flex flex-wrap items-start gap-4">
        <div className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--color-surface)] text-lg font-black text-[var(--color-text-primary)] ring-1 ring-[var(--color-border)]">
          {channel.name.slice(0, 1)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-extrabold text-[var(--color-text-primary)]">
              {channel.name}
            </h1>
            <Badge
              tone={
                channel.status === "available"
                  ? "success"
                  : channel.status === "degraded"
                    ? "warning"
                    : "danger"
              }
              dot
            >
              {channel.status === "available"
                ? "可用"
                : channel.status === "degraded"
                  ? "降级"
                  : "不可用"}
            </Badge>
            <Badge tone="neutral">{channel.stack}</Badge>
            {channel.verified ? (
              <Badge tone="info">数据状态：已核验</Badge>
            ) : null}
            <Badge tone="neutral">更新于 {channel.updatedAt}</Badge>
          </div>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--color-text-muted)]">
            {channel.description}
          </p>
          <p className="mt-1 font-mono text-xs text-[var(--color-text-soft)]">
            {channel.baseUrl}
          </p>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="ChatGPT 综合倍率"
          value={channel.gptRate}
          hint={`${channel.groups.filter((g) => g.name.includes("GPT")).length} 个分组`}
        />
        <StatCard
          label="Claude 综合倍率"
          value={channel.claudeRate}
          hint="演示数据"
        />
        <StatCard
          label="可用率"
          value={channel.availability}
          hint={`样本 ${channel.samples}`}
        />
        <StatCard
          label="最近检查"
          value={channel.lastCheck.split(" ").slice(1).join(" ") || channel.lastCheck}
          hint={channel.lastCheck}
        />
      </div>

      <Panel
        title="分组 / 模型"
        subtitle={`${channel.groups.length} 个分组 · 综合倍率与缓存命中率`}
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--color-border-subtle)] text-xs font-semibold text-[var(--color-text-muted)]">
                <th className="pb-2 font-semibold">分组</th>
                <th className="pb-2 font-semibold">综合倍率</th>
                <th className="pb-2 font-semibold">模型数</th>
                <th className="pb-2 font-semibold">缓存命中率</th>
              </tr>
            </thead>
            <tbody>
              {channel.groups.map((g) => (
                <tr
                  key={g.name}
                  className="border-b border-[var(--color-border-subtle)] last:border-0 hover:bg-[var(--color-surface-hover)]"
                >
                  <td className="py-3 font-semibold text-[var(--color-text-primary)]">
                    {g.name}
                  </td>
                  <td className="py-3 font-extrabold tabular-nums text-[var(--color-text-primary)]">
                    {g.rate}
                  </td>
                  <td className="py-3 tabular-nums text-[var(--color-text-muted)]">
                    {g.models}
                  </td>
                  <td className="py-3 tabular-nums text-[var(--color-text-muted)]">
                    {g.cacheHit}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel
        title="请求日志（含失败）"
        subtitle="与 usage 计费日志分离：503 也会落库"
        action={<Badge tone="warning">mock</Badge>}
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--color-border-subtle)] text-xs font-semibold text-[var(--color-text-muted)]">
                <th className="pb-2">时间</th>
                <th className="pb-2">Request ID</th>
                <th className="pb-2">模型</th>
                <th className="pb-2">状态</th>
                <th className="pb-2">延迟</th>
                <th className="pb-2">错误</th>
              </tr>
            </thead>
            <tbody>
              {channel.recentLogs.map((log) => {
                const ok = log.status >= 200 && log.status < 300;
                return (
                  <tr
                    key={log.id}
                    className="border-b border-[var(--color-border-subtle)] last:border-0 hover:bg-[var(--color-surface-hover)]"
                  >
                    <td className="py-3 tabular-nums text-[var(--color-text-muted)]">
                      {log.time}
                    </td>
                    <td className="py-3 font-mono text-xs text-[var(--color-text-soft)]">
                      {log.id}
                    </td>
                    <td className="py-3 font-semibold text-[var(--color-text-primary)]">
                      {log.model}
                    </td>
                    <td className="py-3">
                      <Badge tone={ok ? "success" : "danger"}>{log.status}</Badge>
                    </td>
                    <td className="py-3 tabular-nums text-[var(--color-text-muted)]">
                      {log.latencyMs}ms
                    </td>
                    <td className="py-3 text-xs text-[var(--color-danger-text)]">
                      {log.error ?? "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
