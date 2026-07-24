import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
}) {
  const styles = {
    primary:
      "bg-[var(--color-primary)] text-[var(--color-text-on-primary)] hover:bg-[var(--color-primary-hover)]",
    secondary:
      "bg-[var(--color-panel)] text-[var(--color-text-body)] border border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]",
    danger:
      "bg-[var(--color-danger-bg)] text-[var(--color-danger-text)] border border-transparent",
    ghost:
      "bg-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-surface)]",
  }[variant];
  return (
    <button
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-semibold transition disabled:opacity-50 ${styles} ${className}`}
      {...props}
    />
  );
}

export function Input({
  className = "",
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={`w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] px-3 py-2 text-sm text-[var(--color-text-body)] outline-none transition placeholder:text-[var(--color-text-placeholder)] focus:border-[var(--color-brand)] ${className}`}
      {...props}
    />
  );
}

export function Select({
  className = "",
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={`w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] px-3 py-2 text-sm text-[var(--color-text-body)] outline-none focus:border-[var(--color-brand)] ${className}`}
      {...props}
    >
      {children}
    </select>
  );
}

export function Field({
  label,
  children,
  help,
}: {
  label: string;
  children: ReactNode;
  help?: string;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-semibold text-[var(--color-text-muted)]">
        {label}
      </span>
      {children}
      {help ? (
        <span className="block text-[11px] leading-relaxed text-[var(--color-text-soft)]">
          {help}
        </span>
      ) : null}
    </label>
  );
}

export function SwitchRow({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-panel-soft)] px-3 py-2.5">
      <span className="text-sm font-semibold text-[var(--color-text-body)]">
        {label}
      </span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 accent-[var(--color-brand)]"
      />
    </label>
  );
}

export function Modal({
  open,
  title,
  subtitle,
  onClose,
  children,
  wide,
}: {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-[var(--color-overlay)] p-4 md:p-8">
      <div
        className={`my-4 w-full rounded-2xl border border-[var(--color-border)] bg-[var(--color-panel)] shadow-[var(--shadow-floating)] ${
          wide ? "max-w-5xl" : "max-w-xl"
        }`}
      >
        <div className="flex items-start justify-between gap-3 border-b border-[var(--color-border-subtle)] px-5 py-4">
          <div>
            <h3 className="text-base font-extrabold text-[var(--color-text-primary)]">
              {title}
            </h3>
            {subtitle ? (
              <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
                {subtitle}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-lg leading-none text-[var(--color-text-muted)] hover:bg-[var(--color-surface)]"
            aria-label="关闭"
          >
            ×
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}
