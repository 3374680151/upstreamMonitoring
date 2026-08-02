import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";

/* 按钮：所有 variant 用同一种「按下 → 抬起」节奏，焦点环统一交给 base layer。 */
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
  const styles = {
    primary:
      "bg-ink-strong text-ink-on-accent border border-ink-strong hover:bg-ink-strong/90",
    brand:
      "bg-accent text-ink-on-accent border border-accent hover:bg-accent-hover shadow-[var(--shadow-pop)]",
    secondary:
      "bg-panel text-ink border border-line hover:bg-sunken-hover hover:border-line-strong",
    danger:
      "bg-danger-bg text-danger-fg border border-danger-fg/30 hover:bg-danger-bg/70 hover:border-danger-fg/55",
    ghost:
      "bg-transparent text-ink-muted border border-transparent hover:bg-sunken hover:text-ink-strong",
  }[variant];
  const sizing = {
    sm: "h-7 px-2.5 text-[12.5px]",
    md: "h-8 px-3 text-[13px]",
  }[size];
  return (
    <button
      type="button"
      aria-busy={loading || undefined}
      className={`inline-flex items-center justify-center gap-1.5 rounded-[var(--radius-sm)] font-medium tracking-[0.01em] transition-[background-color,border-color,transform,box-shadow] duration-[var(--motion-base)] ease-[var(--ease-out-quart)] active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50 aria-disabled:opacity-50 ${sizing} ${styles} ${className}`}
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
      className={`h-8 w-full rounded-[var(--radius-sm)] border border-line bg-panel px-2.5 text-[13px] text-ink outline-none transition-[border-color,box-shadow] duration-[var(--motion-base)] placeholder:text-ink-faint hover:border-line-strong focus:border-accent focus:shadow-[0_0_0_3px_var(--color-accent-ring)] ${className}`}
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
      className={`w-full rounded-[var(--radius-sm)] border border-line bg-panel px-3 py-2 font-mono text-[12.5px] leading-relaxed text-ink outline-none transition-[border-color,box-shadow] duration-[var(--motion-base)] placeholder:text-ink-faint hover:border-line-strong focus:border-accent focus:shadow-[0_0_0_3px_var(--color-accent-ring)] ${className}`}
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
      className={`h-8 w-full cursor-pointer appearance-none rounded-[var(--radius-sm)] border border-line bg-panel px-2.5 pr-7 text-[13px] text-ink outline-none transition-[border-color,box-shadow] duration-[var(--motion-base)] [background-image:var(--select-chevron)] [background-position:right_8px_center] [background-repeat:no-repeat] [background-size:12px_12px] hover:border-line-strong focus:border-accent focus:shadow-[0_0_0_3px_var(--color-accent-ring)] ${className}`}
      style={{
        ...(props.style || {}),
      }}
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
      className="inline-flex min-h-9 gap-0.5 rounded-[var(--radius-sm)] border border-line bg-sunken p-0.5"
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
            className={`min-h-7 shrink-0 rounded-[5px] px-3 text-[12.5px] font-medium transition-[background-color,color] duration-[var(--motion-base)] ${
              selected
                ? "bg-panel text-ink-strong shadow-[var(--shadow-hairline)]"
                : "text-ink-muted hover:text-ink-strong"
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
    <label className="flex flex-col gap-1.5">
      <span className="t-small font-medium text-ink-muted">{label}</span>
      {children}
      {help ? (
        <span className="text-[11.5px] leading-relaxed text-ink-soft">
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
      className={`flex w-full items-center justify-between gap-3 rounded-[var(--radius-md)] border px-3 py-2.5 text-left transition-[border-color,background-color] duration-[var(--motion-base)] ${
        checked
          ? "border-accent/40 bg-accent-soft/60"
          : "border-line bg-panel-soft hover:border-line-strong"
      }`}
    >
      <span className="text-[13.5px] font-medium text-ink">{label}</span>
      <span
        className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-[var(--motion-base)] ${
          checked ? "bg-accent" : "bg-sunken-active"
        }`}
        aria-hidden
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-paper shadow-[var(--shadow-pop)] transition-transform duration-[var(--motion-base)] ${
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
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-overlay px-4 py-6 backdrop-blur-[3px] md:px-8 md:py-10">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`my-auto w-full overflow-hidden rounded-[var(--radius-xl)] border border-line bg-panel shadow-[var(--shadow-floating)] ${
          wide ? "max-w-5xl" : "max-w-xl"
        }`}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line bg-panel-soft px-5 py-4">
          <div className="min-w-0">
            <h3 className="t-title font-serif tracking-[-0.01em]">{title}</h3>
            {subtitle ? (
              <p className="mt-1 text-[12.5px] leading-relaxed text-ink-muted">
                {subtitle}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--radius-sm)] text-[15px] leading-none text-ink-muted transition-colors duration-[var(--motion-fast)] hover:bg-sunken hover:text-ink-strong"
            aria-label="关闭"
          >
            ×
          </button>
        </div>
        <div className="px-5 py-5">{children}</div>
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
      <div className="flex flex-col gap-4">
        <div className="text-[13.5px] leading-relaxed text-ink">{message}</div>
        {error ? (
          <div className="rounded-[var(--radius-sm)] border border-danger-fg/25 bg-danger-bg px-3 py-2 text-[12.5px] text-danger-fg">
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

/* 把颜色变体收敛到 token，避免组件分散在各处拼 var(--color-…)。 */
export const colorTokens = {
  page: "var(--color-page)",
  paper: "var(--color-paper)",
  panel: "var(--color-panel)",
  panelSoft: "var(--color-panel-soft)",
  sunken: "var(--color-sunken)",
  sunkenHover: "var(--color-sunken-hover)",
  sunkenActive: "var(--color-sunken-active)",
  overlay: "var(--color-overlay)",

  ink: "var(--color-ink)",
  inkStrong: "var(--color-ink-strong)",
  inkMuted: "var(--color-ink-muted)",
  inkSoft: "var(--color-ink-soft)",
  inkFaint: "var(--color-ink-faint)",
  inkOnAccent: "var(--color-ink-on-accent)",

  line: "var(--color-line)",
  lineSoft: "var(--color-line-soft)",
  lineStrong: "var(--color-line-strong)",

  accent: "var(--color-accent)",
  accentHover: "var(--color-accent-hover)",
  accentSoft: "var(--color-accent-soft)",
  accentRing: "var(--color-accent-ring)",

  successFg: "var(--color-success-fg)",
  successBg: "var(--color-success-bg)",
  warningFg: "var(--color-warning-fg)",
  warningBg: "var(--color-warning-bg)",
  infoFg: "var(--color-info-fg)",
  infoBg: "var(--color-info-bg)",
  dangerFg: "var(--color-danger-fg)",
  dangerBg: "var(--color-danger-bg)",
};
