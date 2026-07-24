import { Panel } from "@/components/Panel";

export function PlaceholderPage({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold text-[var(--color-text-primary)]">
          {title}
        </h1>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">
          {description}
        </p>
      </div>
      <Panel title="待实现" subtitle="脚手架占位">
        <p className="text-sm text-[var(--color-text-muted)]">
          后端 API 与数据接入后在此展示。全局规则见仓库根目录{" "}
          <code className="rounded bg-[var(--color-surface)] px-1.5 py-0.5 text-xs">
            AGENTS.md
          </code>
          ，UI 规范见 skill{" "}
          <code className="rounded bg-[var(--color-surface)] px-1.5 py-0.5 text-xs">
            priceai-ui
          </code>
          。
        </p>
      </Panel>
    </div>
  );
}
