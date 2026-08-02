import type { ReactNode } from "react";

export function PageHeader({
  title,
  subtitle,
  action,
  large,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  large?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
      <div className="flex items-end gap-4">
        <span
          className="mb-1 h-10 w-[2px] shrink-0 rounded-full bg-accent"
          aria-hidden
        />
        <div className="min-w-0">
          <h1
            className={`font-serif font-semibold tracking-[-0.02em] text-ink-strong ${
              large ? "text-[28px] leading-[1.1]" : "text-[24px] leading-[1.15]"
            } md:${large ? "text-[32px]" : "text-[26px]"}`}
          >
            {title}
          </h1>
          {subtitle ? (
            <p className="mt-2 max-w-3xl text-[13px] leading-relaxed text-ink-muted">
              {subtitle}
            </p>
          ) : null}
        </div>
      </div>
      {action ? (
        <div className="w-full min-w-0 shrink-0 sm:w-auto">{action}</div>
      ) : null}
    </div>
  );
}
