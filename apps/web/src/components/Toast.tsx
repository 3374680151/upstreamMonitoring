import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";

type ToastKind = "success" | "error" | "info";

type Toast = {
  id: number;
  kind: ToastKind;
  message: string;
};

/** 失败要看清原因，所以停留更久；成功一眼扫过即可。 */
const TTL: Record<ToastKind, number> = {
  success: 3000,
  info: 4000,
  error: 8000,
};

const MAX_VISIBLE = 4;

type ToastApi = {
  success: (message: string) => void;
  error: (message: unknown) => void;
  info: (message: string) => void;
  /**
   * 包一个异步动作：无论成功失败都必定给出提示。
   * 成功返回结果，失败**吞掉异常**并弹错误提示（返回 undefined），
   * 这样调用点不会因为忘了 try/catch 而「点了没反应」。
   */
  run: <T>(
    action: () => Promise<T>,
    opts?: { success?: string; failure?: string },
  ) => Promise<T | undefined>;
};

const ToastContext = createContext<ToastApi | null>(null);

export function errorText(err: unknown, fallback = "操作失败"): string {
  if (err instanceof Error) return err.message || fallback;
  if (typeof err === "string" && err.trim()) return err;
  return fallback;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (kind: ToastKind, message: string) => {
      const text = message.trim();
      if (!text) return;
      const id = ++seq.current;
      setToasts((prev) => [...prev, { id, kind, message: text }].slice(-MAX_VISIBLE));
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), TTL[kind]),
      );
    },
    [dismiss],
  );

  // 卸载时清掉所有计时器，避免对已卸载组件 setState
  useEffect(() => {
    const pending = timers.current;
    return () => {
      pending.forEach((t) => clearTimeout(t));
      pending.clear();
    };
  }, []);

  const api = useMemo<ToastApi>(() => {
    const success = (m: string) => push("success", m);
    const error = (m: unknown) => push("error", errorText(m));
    const info = (m: string) => push("info", m);
    return {
      success,
      error,
      info,
      run: async (action, opts) => {
        try {
          const result = await action();
          if (opts?.success) success(opts.success);
          return result;
        } catch (err) {
          error(errorText(err, opts?.failure || "操作失败"));
          return undefined;
        }
      },
    };
  }, [push]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast 必须在 <ToastProvider> 内使用");
  return ctx;
}

const KIND_STYLE: Record<
  ToastKind,
  { wrap: string; icon: ReactNode; role: "status" | "alert" }
> = {
  success: {
    wrap: "border-[var(--color-brand)]/40 bg-[var(--color-success-bg)] text-[var(--color-success-text)]",
    icon: <CheckCircle2 size={16} />,
    role: "status",
  },
  error: {
    wrap: "border-[var(--color-danger-text)]/40 bg-[var(--color-danger-bg)] text-[var(--color-danger-text)]",
    icon: <AlertTriangle size={16} />,
    role: "alert",
  },
  info: {
    wrap: "border-[var(--color-border)] bg-[var(--color-panel)] text-[var(--color-text-body)]",
    icon: <Info size={16} />,
    role: "status",
  },
};

function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div
      className="pointer-events-none fixed inset-x-3 bottom-3 z-[100] flex flex-col items-stretch gap-2 sm:inset-x-auto sm:right-4 sm:bottom-4 sm:w-[min(380px,calc(100vw-2rem))]"
      aria-live="polite"
    >
      {toasts.map((t) => {
        const style = KIND_STYLE[t.kind];
        return (
          <div
            key={t.id}
            role={style.role}
            className={`upstream-toast pointer-events-auto flex items-start gap-2 rounded-xl border px-3.5 py-2.5 text-sm font-medium shadow-[var(--shadow-surface)] backdrop-blur-sm ${style.wrap}`}
          >
            <span className="mt-0.5 shrink-0" aria-hidden>
              {style.icon}
            </span>
            <span className="min-w-0 flex-1 break-words">{t.message}</span>
            <button
              type="button"
              className="-mr-1 shrink-0 rounded p-0.5 opacity-60 transition hover:opacity-100"
              onClick={() => onDismiss(t.id)}
              aria-label="关闭提示"
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
