import { useState } from "react";
import { api, setConsoleToken } from "@/lib/api";
import { Button, Input } from "@/components/ui";
import { errorText, useToast } from "@/components/Toast";

export function LoginPage({ onSuccess }: { onSuccess: () => void }) {
  const toast = useToast();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!password) {
      setError("请输入密码");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const resp = await api.login(password);
      if (!resp.success || !resp.token) {
        throw new Error(resp.message || "登录失败");
      }
      setConsoleToken(resp.token);
      toast.success("登录成功");
      onSuccess();
    } catch (err) {
      const message = errorText(err, "登录失败");
      setError(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-page)] px-4">
      <div className="w-full max-w-sm rounded-2xl border border-[var(--color-border)] bg-[var(--color-panel)] p-6 shadow-[var(--shadow-floating)]">
        <div className="mb-5 flex items-center gap-2.5">
          <span
            className="inline-flex h-9 w-9 items-center justify-center rounded-xl text-sm font-black text-white shadow-[0_6px_16px_#45bf7855]"
            style={{ backgroundImage: "linear-gradient(135deg, #52cc86 0%, #2f9d63 100%)" }}
          >
            U
          </span>
          <div className="leading-tight">
            <div className="text-sm font-extrabold tracking-tight text-[var(--color-text-primary)]">
              Upstream 控制台
            </div>
            <div className="text-[11px] font-semibold text-[var(--color-text-muted)]">
              请输入访问密码
            </div>
          </div>
        </div>

        <label className="block space-y-1.5">
          <span className="text-xs font-semibold text-[var(--color-text-muted)]">
            控制台密码
          </span>
          <Input
            type="password"
            value={password}
            autoFocus
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
            placeholder="CONSOLE_PASSWORD"
          />
        </label>

        {error ? (
          <div className="mt-3 rounded-lg bg-[var(--color-danger-bg)] px-3 py-2 text-xs text-[var(--color-danger-text)]">
            {error}
          </div>
        ) : null}

        <Button className="mt-5 w-full" onClick={submit} loading={busy}>
          {busy ? "登录中..." : "登录"}
        </Button>

        <p className="mt-4 text-[11px] leading-relaxed text-[var(--color-text-soft)]">
          密码在服务端通过环境变量 <code>CONSOLE_PASSWORD</code> 设置。留空该变量则不启用登录（仅建议本地/内网直连时）。
        </p>
      </div>
    </div>
  );
}
