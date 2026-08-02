import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  loading = false,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost" | "brand";
  size?: "sm" | "md";
  /** 请求进行中：显示转圈并锁住自己，让「点了没反应」变成可见的进行中状态 */
  loading?: boolean;
}) {
  // 每个 variant 都带可见边框：按钮边界始终清晰，不依赖背景色差
  const styles = {
    primary:
      "bg-[var(--color-primary)] text-[var(--color-text-on-primary)] border border-[var(--color-primary-strong)] shadow-[var(--shadow-control)] hover:bg-[var(--color-primary-hover)]",
    brand:
      "text-white border border-[#2f9d63] shadow-[0_6px_16px_#45bf7855] hover:brightness-[1.06] [background-image:linear-gradient(135deg,#52cc86_0%,#2f9d63_100%)]",
    secondary:
      "bg-[var(--color-panel)] text-[var(--color-text-body)] border border-[var(--color-border)] hover:bg-[var(--color-surface-hover)] hover:border-[var(--color-border-muted)]",
    danger:
      "bg-[var(--color-danger-bg)] text-[var(--color-danger-text)] border border-[var(--color-danger-text)]/35 hover:brightness-95 hover:border-[var(--color-danger-text)]/55",
    ghost:
      "bg-transparent text-[var(--color-text-muted)] border border-[var(--color-border)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-surface)] hover:border-[var(--color-border-muted)]",
  }[variant];
  const sizing = {
    sm: "px-2.5 py-1 text-xs",
    md: "px-3 py-1.5 text-sm",
  }[size];
  return (
    <button
      type="button"
      aria-busy={loading || undefined}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-semibold transition-all duration-200 outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand)] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--color-panel)] active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50 aria-disabled:opacity-50 ${sizing} ${styles} ${className}`}
      {...props}
      disabled={props.disabled || loading}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
}

/** 内联转圈：给「几秒才回来」的按钮一个 100ms 内可见的进行中信号 */
export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-[1.5px] border-current border-t-transparent ${className}`}
      aria-hidden
    />
  );
}

export function Input({
  className = "",
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={`w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] px-3 py-2 text-sm text-[var(--color-text-body)] outline-none transition duration-200 placeholder:text-[var(--color-text-placeholder)] hover:border-[var(--color-border-muted)] focus:border-[var(--color-brand)] focus:ring-4 focus:ring-[var(--color-brand)]/15 ${className}`}
      {...props}
    />
  );
}

export function Textarea({
  className = "",
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={`w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] px-3 py-2 font-mono text-xs leading-relaxed text-[var(--color-text-body)] outline-none transition duration-200 placeholder:text-[var(--color-text-placeholder)] hover:border-[var(--color-border-muted)] focus:border-[var(--color-brand)] focus:ring-4 focus:ring-[var(--color-brand)]/15 ${className}`}
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
      className={`w-full cursor-pointer rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] px-3 py-2 text-sm text-[var(--color-text-body)] outline-none transition duration-200 hover:border-[var(--color-border-muted)] focus:border-[var(--color-brand)] focus:ring-4 focus:ring-[var(--color-brand)]/15 ${className}`}
      {...props}
    >
      {children}
    </select>
  );
}

export function Tabs<T extends string>({
  items,
  value,
  onChange,
  label,
}: {
  items: Array<{ id: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className="flex min-h-10 gap-1 overflow-x-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1"
    >
      {items.map((item) => {
        const selected = item.id === value;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={`tab-panel-${item.id}`}
            onClick={() => onChange(item.id)}
            className={`min-h-8 shrink-0 rounded-md px-3 text-xs font-semibold transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand)] ${
              selected
                ? "bg-[var(--color-panel)] text-[var(--color-text-primary)] shadow-[var(--shadow-control)]"
                : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text-body)]"
            }`}
          >
            {item.label}
          </button>
        );
      })}
    </div>
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
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`flex w-full items-center justify-between gap-3 rounded-xl border px-3 py-2.5 text-left transition duration-200 outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand)] ${
        checked
          ? "border-[var(--color-brand)]/40 bg-[var(--color-success-bg)]"
          : "border-[var(--color-border)] bg-[var(--color-panel-soft)] hover:border-[var(--color-border-muted)]"
      }`}
    >
      <span className="text-sm font-semibold text-[var(--color-text-body)]">
        {label}
      </span>
      <span
        className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-200 ${
          checked ? "bg-[var(--color-brand)]" : "bg-[var(--color-surface-muted)]"
        }`}
        aria-hidden
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-200 ${
            checked ? "translate-x-4" : "translate-x-0.5"
          }`}
        />
      </span>
    </button>
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
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-[var(--color-overlay)] p-4 md:p-8">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`my-4 w-full rounded-2xl border border-[var(--color-border)] bg-[var(--color-panel)] shadow-[var(--shadow-floating)] ${
          wide ? "max-w-5xl" : "max-w-xl"
        }`}
      >
        <div className="flex items-start justify-between gap-3 rounded-t-2xl border-b border-[var(--color-border-subtle)] bg-[var(--color-panel-soft)] px-5 py-4">
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
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-lg leading-none text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface)] hover:text-[var(--color-text-primary)]"
            aria-label="关闭"
          >
            ×
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

/** 危险操作确认框。
 *
 * 刻意不用 window.confirm：内嵌 WebView / 被浏览器抑制弹窗的场景里 confirm() 会
 * 直接返回 false，于是「删除」点了完全没反应且没有任何提示。自绘弹窗在任何环境
 * 里都会出现，还能显示进行中状态和失败原因。 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "确认",
  cancelLabel = "取消",
  danger,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  busy?: boolean;
  error?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal open={open} title={title} onClose={busy ? () => {} : onCancel}>
      <div className="space-y-4">
        <div className="text-sm leading-relaxed text-[var(--color-text-body)]">
          {message}
        </div>
        {error ? (
          <div className="rounded-lg bg-[var(--color-danger-bg)] px-3 py-2 text-xs text-[var(--color-danger-text)]">
            {error}
          </div>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            variant={danger ? "danger" : "primary"}
            onClick={onConfirm}
            loading={busy}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
