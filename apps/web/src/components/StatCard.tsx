import type { ReactNode } from "react";

export function StatCard({
  label,
  value,
  hint,
  accent = true,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  accent?: boolean;
}) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-panel)] p-4 shadow-[var(--shadow-surface)] md:p-5">
      {accent ? (
        <span
          className="absolute top-4 bottom-4 left-0 w-[4px] rounded-full bg-[var(--color-brand)]"
          aria-hidden
        />
      ) : null}
      <div className={accent ? "pl-3" : undefined}>
        <div className="text-xs font-semibold text-[var(--color-text-muted)]">
          {label}
        </div>
        <div className="mt-1 text-2xl font-extrabold tabular-nums leading-tight text-[var(--color-text-primary)] md:text-3xl">
          {value}
        </div>
        {hint ? (
          <div className="mt-1 text-xs text-[var(--color-text-soft)]">{hint}</div>
        ) : null}
      </div>
    </div>
  );
}
