/**
 * Toast 全局提示系统。
 *
 * 模块级 reactive 单例：任意组件调 useToast() 都拿到同一组方法，
 * ToastViewport.vue 调 useToastState() 拿到同一份只读列表。
 */
import { reactive, readonly } from "vue";

type ToastKind = "success" | "error" | "info";

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

/** 失败要看清原因，所以停留更久；成功一眼扫过即可。 */
const TTL: Record<ToastKind, number> = {
  success: 3000,
  info: 4000,
  error: 8000,
};

const MAX_VISIBLE = 4;

const state = reactive<{ toasts: ToastItem[] }>({ toasts: [] });
let seq = 0;
const timers = new Map<number, ReturnType<typeof setTimeout>>();

export function errorText(err: unknown, fallback = "操作失败"): string {
  if (err instanceof Error) return err.message || fallback;
  if (typeof err === "string" && err.trim()) return err;
  return fallback;
}

function dismiss(id: number): void {
  const idx = state.toasts.findIndex((t) => t.id === id);
  if (idx >= 0) state.toasts.splice(idx, 1);
  const timer = timers.get(id);
  if (timer) {
    clearTimeout(timer);
    timers.delete(id);
  }
}

function push(kind: ToastKind, message: string): void {
  const text = message.trim();
  if (!text) return;
  const id = ++seq;
  state.toasts.push({ id, kind, message: text });
  if (state.toasts.length > MAX_VISIBLE) {
    state.toasts.splice(0, state.toasts.length - MAX_VISIBLE);
  }
  timers.set(id, setTimeout(() => dismiss(id), TTL[kind]));
}

export function useToast() {
  return {
    success: (m: string) => push("success", m),
    error: (m: unknown) => push("error", errorText(m)),
    info: (m: string) => push("info", m),
    run: async <T>(
      action: () => Promise<T>,
      opts?: { success?: string; failure?: string },
    ): Promise<T | undefined> => {
      try {
        const result = await action();
        if (opts?.success) push("success", opts.success);
        return result;
      } catch (err) {
        push("error", errorText(err, opts?.failure || "操作失败"));
        return undefined;
      }
    },
  };
}

/** ToastViewport 消费：只读 toasts + dismiss */
export function useToastState() {
  return { toasts: readonly(state).toasts };
}

export function dismissToast(id: number): void {
  dismiss(id);
}
