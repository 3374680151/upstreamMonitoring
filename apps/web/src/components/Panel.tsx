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
      className={`min-h-0 min-w-0 overflow-hidden rounded-[var(--radius-lg)] border border-line bg-panel shadow-[var(--shadow-hairline)] transition-[border-color] duration-[var(--motion-base)] hover:border-line-strong ${className}`}
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line-soft bg-panel-soft px-5 py-3.5">
        <div className="min-w-0">
          <h2 className="font-serif text-[15.5px] font-semibold tracking-[-0.01em] text-ink-strong">
            {title}
          </h2>
          {subtitle ? (
            <p className="mt-0.5 text-[12px] leading-relaxed text-ink-muted">
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
      <div className="flex min-h-0 min-w-0 flex-1 flex-col p-4 md:p-5">{children}</div>
    </section>
  );
}
