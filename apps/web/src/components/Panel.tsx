import type { ReactNode } from "react";

export function Panel({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`min-h-0 min-w-0 overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-panel)] shadow-[var(--shadow-surface)] transition-colors duration-200 hover:border-[var(--color-border-muted)] ${className}`}
    >
      <header className="flex flex-wrap items-start justify-between gap-3 rounded-t-2xl border-b border-[var(--color-border-subtle)] bg-[var(--color-panel-soft)] px-4 py-3 md:px-5">
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
        {/* 窄屏下 action 里可能塞了下拉+搜索框+多个按钮。用 shrink-0 会让它拒绝收缩，
            把整页撑出横向滚动条；这里允许收缩并在放不下时独占一行。 */}
        {action ? (
          <div className="min-w-0 max-w-full shrink-0 max-sm:w-full max-sm:shrink">
            {action}
          </div>
        ) : null}
      </header>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col p-4 md:p-5">
        {children}
      </div>
    </section>
  );
}
