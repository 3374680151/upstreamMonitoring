import { Link } from "react-router-dom";
import { Badge } from "@/components/Badge";
import { Panel } from "@/components/Panel";
import { channels } from "@/lib/mock";

export function ChannelsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold text-[var(--color-text-primary)]">
          上游渠道
        </h1>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">
          管理可转发的上游 OpenAI-compatible 端点。
        </p>
      </div>

      <Panel title="全部渠道" subtitle={`${channels.length} 条`}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--color-border-subtle)] text-xs font-semibold text-[var(--color-text-muted)]">
                <th className="pb-2">名称</th>
                <th className="pb-2">状态</th>
                <th className="pb-2">可用率</th>
                <th className="pb-2">GPT 倍率</th>
                <th className="pb-2">最近检查</th>
              </tr>
            </thead>
            <tbody>
              {channels.map((c) => (
                <tr
                  key={c.id}
                  className="border-b border-[var(--color-border-subtle)] last:border-0 hover:bg-[var(--color-surface-hover)]"
                >
                  <td className="py-3">
                    <Link
                      to={`/channels/${c.id}`}
                      className="font-bold text-[var(--color-text-primary)] hover:underline"
                    >
                      {c.name}
                    </Link>
                    <div className="font-mono text-[11px] text-[var(--color-text-soft)]">
                      {c.baseUrl}
                    </div>
                  </td>
                  <td className="py-3">
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
                      {c.status}
                    </Badge>
                  </td>
                  <td className="py-3 font-extrabold tabular-nums">
                    {c.availability}
                  </td>
                  <td className="py-3 font-extrabold tabular-nums">{c.gptRate}</td>
                  <td className="py-3 text-[var(--color-text-muted)]">
                    {c.lastCheck}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
