import type { ReactNode } from "react";

type StatTone = "brand" | "neutral" | "danger" | "info" | "warning";

const toneStyles: Record<
  StatTone,
  { bar: string; chip: string }
> = {
  brand: {
    bar: "bg-[var(--color-brand)]",
    chip: "bg-[var(--color-success-bg)] text-[var(--color-success-text)]",
  },
  neutral: {
    bar: "bg-[var(--color-border)]",
    chip: "bg-[var(--color-surface)] text-[var(--color-text-muted)]",
  },
  danger: {
    bar: "bg-[var(--color-danger-text)]",
    chip: "bg-[var(--color-danger-bg)] text-[var(--color-danger-text)]",
  },
  info: {
    bar: "bg-[var(--color-info-text)]",
    chip: "bg-[var(--color-info-bg)] text-[var(--color-info-text)]",
  },
  warning: {
    bar: "bg-[var(--color-warning-text)]",
    chip: "bg-[var(--color-warning-bg)] text-[var(--color-warning-text)]",
  },
};

export function StatCard({
  label,
  value,
  hint,
  icon,
  tone = "brand",
  accent = true,
  className = "",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
  tone?: StatTone;
  accent?: boolean;
  className?: string;
}) {
  const styles = toneStyles[tone];
  return (
    <div
    className={`group relative overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-panel)] p-4 shadow-[var(--shadow-surface)] transition duration-200 hover:border-[var(--color-border-muted)] hover:shadow-[var(--shadow-control)] md:p-5 ${className}`}
    >
      {accent ? (
        <span
          className={`absolute top-4 bottom-4 left-0 w-[3px] rounded-full ${styles.bar}`}
          aria-hidden
        />
      ) : null}
      <div className={`relative flex items-start justify-between gap-3 ${accent ? "pl-3" : ""}`}>
        <div className="min-w-0">
          <div className="text-xs font-semibold text-[var(--color-text-muted)]">
            {label}
          </div>
          <div className="mt-1.5 text-2xl font-extrabold tabular-nums leading-none text-[var(--color-text-primary)] md:text-3xl">
            {value}
          </div>
          {hint ? (
            <div className="mt-2 text-xs text-[var(--color-text-soft)]">
              {hint}
            </div>
          ) : null}
        </div>
        {icon ? (
          <span
            className={`inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition duration-300 group-hover:scale-105 ${styles.chip}`}
            aria-hidden
          >
            {icon}
          </span>
        ) : null}
      </div>
    </div>
  );
}
