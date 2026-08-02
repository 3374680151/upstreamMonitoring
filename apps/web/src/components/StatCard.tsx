import type { ReactNode } from "react";

type StatTone = "brand" | "neutral" | "danger" | "info" | "warning";

const toneStyles: Record<
  StatTone,
  { bar: string; chip: string }
> = {
  brand: {
    bar: "bg-accent",
    chip: "bg-success-bg text-success-fg",
  },
  neutral: {
    bar: "bg-ink-faint",
    chip: "bg-sunken text-ink-muted",
  },
  danger: {
    bar: "bg-danger-fg",
    chip: "bg-danger-bg text-danger-fg",
  },
  info: {
    bar: "bg-info-fg",
    chip: "bg-info-bg text-info-fg",
  },
  warning: {
    bar: "bg-warning-fg",
    chip: "bg-warning-bg text-warning-fg",
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
      className={`group relative overflow-hidden rounded-[var(--radius-md)] border border-line bg-panel p-3.5 shadow-[var(--shadow-hairline)] transition-[border-color,box-shadow] duration-[var(--motion-base)] hover:border-line-strong hover:shadow-[var(--shadow-pop)] md:p-4 ${className}`}
    >
      {accent ? (
        <span
          className={`absolute top-3 bottom-3 left-0 w-[2px] rounded-full ${styles.bar}`}
          aria-hidden
        />
      ) : null}
      <div
        className={`relative flex items-start justify-between gap-3 ${
          accent ? "pl-2.5" : ""
        }`}
      >
        <div className="min-w-0">
          <div className="t-micro">{label}</div>
          <div className="mt-1.5 font-serif text-[24px] leading-none tabular tracking-[-0.02em] text-ink-strong md:text-[26px]">
            {value}
          </div>
          {hint ? (
            <div className="mt-1.5 text-[11.5px] leading-tight text-ink-soft">
              {hint}
            </div>
          ) : null}
        </div>
        {icon ? (
          <span
            className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-sm)] transition-transform duration-[var(--motion-base)] group-hover:scale-105 ${styles.chip}`}
            aria-hidden
          >
            {icon}
          </span>
        ) : null}
      </div>
    </div>
  );
}
