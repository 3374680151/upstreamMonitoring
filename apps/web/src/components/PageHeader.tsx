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
    <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
      <div className="flex items-start gap-3">
        <span
          className="mt-1 h-8 w-1 shrink-0 rounded-full bg-[var(--color-brand)]"
          aria-hidden
        />
        <div className="min-w-0">
          <h1
            className={`font-sans font-extrabold leading-tight text-[var(--color-text-primary)] ${
              large ? "text-xl sm:text-2xl lg:text-3xl" : "text-xl sm:text-2xl"
            }`}
          >
            {title}
          </h1>
          {subtitle ? (
            <p className="mt-1 max-w-3xl text-sm leading-6 text-[var(--color-text-muted)]">
              {subtitle}
            </p>
          ) : null}
        </div>
      </div>
      {action ? <div className="w-full min-w-0 shrink-0 sm:w-auto">{action}</div> : null}
    </div>
  );
}
