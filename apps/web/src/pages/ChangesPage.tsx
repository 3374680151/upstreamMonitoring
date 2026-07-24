import { Panel } from "@/components/Panel";
import { ChangeTable } from "@/components/ChangeTable";
import type { Change, Site } from "@/lib/types";

export function ChangesPage({
  changes,
  sites,
}: {
  changes: Change[];
  sites: Site[];
}) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-serif text-2xl font-extrabold text-[var(--color-text-primary)]">
          变化记录
        </h1>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">
          所有站点最近变化，方便快速扫一眼是否有上游改价。
        </p>
      </div>
      <Panel title="全局变化记录" subtitle={`${changes.length} 条`}>
        <ChangeTable changes={changes} sites={sites} />
      </Panel>
    </div>
  );
}
