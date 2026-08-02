import { useEffect, useState } from "react";
import type { Channel } from "@/lib/types";
import { parseChannelPriority } from "@/lib/channelPriority";
import { Button, Field, Input, Modal } from "./ui";

export function ChannelPriorityDialog({
  open,
  channel,
  onClose,
  onSubmit,
}: {
  open: boolean;
  channel: Channel | null;
  onClose: () => void;
  onSubmit: (priority: number) => Promise<void>;
}) {
  const [priorityInput, setPriorityInput] = useState("0");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setPriorityInput(String(channel?.priority ?? 0));
    setError("");
  }, [open, channel?.id]);

  async function save() {
    const priority = parseChannelPriority(priorityInput);
    if (priority === null) {
      setError("请输入有效的整数优先级");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onSubmit(priority);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      title={`编辑优先级 · ${channel?.name || channel?.id || "渠道"}`}
      subtitle="仅调整主站调度优先级，其他渠道配置保持不变"
      onClose={onClose}
    >
      <Field label="优先级 priority" help="数值越大越优先被调度">
        <Input
          type="number"
          step={1}
          value={priorityInput}
          onChange={(event) => setPriorityInput(event.target.value)}
        />
      </Field>
      {error ? (
        <div className="mt-3 rounded-[var(--radius-sm)] border border-danger-fg/25 bg-danger-bg px-3 py-2 text-[12.5px] text-danger-fg">
          {error}
        </div>
      ) : null}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose} disabled={saving}>
          取消
        </Button>
        <Button onClick={save} loading={saving}>
          保存优先级
        </Button>
      </div>
    </Modal>
  );
}
