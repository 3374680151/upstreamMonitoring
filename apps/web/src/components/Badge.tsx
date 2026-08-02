import type { ReactNode } from "react";

type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "brand";

const toneClass: Record<Tone, string> = {
  neutral: "bg-sunken text-ink-muted border-line",
  success: "bg-success-bg text-success-fg border-transparent",
  warning: "bg-warning-bg text-warning-fg border-transparent",
  danger: "bg-danger-bg text-danger-fg border-transparent",
  info: "bg-info-bg text-info-fg border-transparent",
  brand: "bg-accent text-ink-on-accent border-transparent",
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
      className={`inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border px-2 py-0.5 text-[11px] font-medium tracking-[0.01em] whitespace-nowrap leading-none ${toneClass[tone]} ${className}`}
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
