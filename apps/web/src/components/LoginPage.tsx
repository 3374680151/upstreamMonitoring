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
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-[400px] overflow-hidden rounded-[var(--radius-xl)] border border-line bg-panel shadow-[var(--shadow-floating)]">
        <div className="border-b border-line-soft bg-panel-soft px-7 py-6">
          <div className="flex items-center gap-3">
            <span
              className="inline-flex h-10 w-10 items-center justify-center rounded-[10px] font-serif text-[17px] font-semibold text-ink-on-accent shadow-[var(--shadow-pop)]"
              style={{
                backgroundImage:
                  "linear-gradient(135deg, #2c8a5a 0%, #1f6e47 100%)",
              }}
            >
              U
            </span>
            <div className="leading-tight">
              <div className="font-serif text-[16px] font-semibold tracking-[-0.01em] text-ink-strong">
                Upstream 控制台
              </div>
              <div className="t-micro mt-0.5">访问受密码保护</div>
            </div>
          </div>
        </div>

        <form
          className="flex flex-col gap-6 md:gap-8 px-7 py-6"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <label className="flex flex-col gap-1.5">
            <span className="t-small font-medium text-ink-muted">
              控制台密码
            </span>
            <Input
              type="password"
              value={password}
              autoFocus
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              placeholder="在服务端 .env 中设置"
            />
          </label>

          {error ? (
            <div className="rounded-[var(--radius-sm)] border border-danger-fg/25 bg-danger-bg px-3 py-2 text-[12.5px] text-danger-fg">
              {error}
            </div>
          ) : null}

          <Button
            type="submit"
            variant="brand"
            className="h-9 w-full text-[13.5px]"
            loading={busy}
          >
            {busy ? "登录中..." : "登录"}
          </Button>

          <p className="text-[11.5px] leading-relaxed text-ink-soft">
            密码在服务端通过环境变量 <code className="font-mono">CONSOLE_PASSWORD</code> 设置。留空该变量则不启用登录（仅建议本地/内网直连时）。
          </p>
        </form>
      </div>
    </div>
  );
}
