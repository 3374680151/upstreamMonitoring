import type { ReactNode } from "react";

type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "brand";

const toneClass: Record<Tone, string> = {
  neutral:
    "bg-[var(--color-surface)] text-[var(--color-text-muted)] border-[var(--color-border)]",
  success:
    "bg-[var(--color-success-bg)] text-[var(--color-success-text)] border-transparent",
  warning:
    "bg-[var(--color-warning-bg)] text-[var(--color-warning-text)] border-transparent",
  danger:
    "bg-[var(--color-danger-bg)] text-[var(--color-danger-text)] border-transparent",
  info: "bg-[var(--color-info-bg)] text-[var(--color-info-text)] border-transparent",
  brand:
    "bg-[var(--color-brand)] text-[var(--color-text-on-primary)] border-transparent",
};

export function Badge({
  children,
  tone = "neutral",
  dot,
  className = "",
  title,
}: {
  children: ReactNode;
  tone?: Tone;
  dot?: boolean;
  className?: string;
  title?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[10px] font-semibold whitespace-nowrap ${toneClass[tone]} ${className}`}
      title={title}
    >
      {dot ? (
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full bg-current"
          aria-hidden
        />
      ) : null}
      {children}
    </span>
  );
}
