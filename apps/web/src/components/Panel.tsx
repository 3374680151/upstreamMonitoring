import type { ReactNode } from "react";

export function Panel({
  title,
  subtitle,
  action,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-panel)] shadow-[var(--shadow-surface)]">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--color-border-subtle)] px-4 py-3 md:px-5">
        <div className="min-w-0">
          <h2 className="text-sm font-bold text-[var(--color-text-primary)]">
            {title}
          </h2>
          {subtitle ? (
            <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
              {subtitle}
            </p>
          ) : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </header>
      <div className="p-4 md:p-5">{children}</div>
    </section>
  );
}
