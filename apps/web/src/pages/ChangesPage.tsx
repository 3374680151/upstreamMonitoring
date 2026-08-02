import { PageHeader } from "@/components/PageHeader";
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
    <div className="upstream-rise space-y-6">
      <PageHeader
        title="变化记录"
        subtitle="所有渠道最近变化，方便快速扫一眼是否有上游改价。"
      />
      <Panel title="全局变化记录" subtitle={`${changes.length} 条`}>
        <ChangeTable changes={changes} sites={sites} />
      </Panel>
    </div>
  );
}
