import { Link, NavLink } from "react-router-dom";
import { Moon, Sun } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";

const nav = [
  { to: "/", label: "概览" },
  { to: "/channels", label: "上游渠道" },
  { to: "/logs", label: "请求日志" },
  { to: "/probes", label: "监测样本" },
];

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const saved = localStorage.getItem("upstream-theme");
    if (saved === "dark" || saved === "light") return saved;
    return "light";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("upstream-theme", theme);
  }, [theme]);

  return {
    theme,
    toggle: () => setTheme((t) => (t === "light" ? "dark" : "light")),
  };
}

export function AppShell({ children }: { children: ReactNode }) {
  const { theme, toggle } = useTheme();

  return (
    <div className="min-h-full bg-[var(--color-page)] text-[var(--color-text-body)]">
      <header className="sticky top-0 z-40 border-b border-[var(--color-border)] bg-[var(--color-page-translucent)] backdrop-blur-sm">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-3 md:px-6">
          <Link to="/" className="flex shrink-0 items-center gap-2">
            <span className="inline-flex h-8 w-8 items-center justify-center rounded-xl bg-[var(--color-brand)] text-sm font-black text-[var(--color-text-on-primary)]">
              U
            </span>
            <div className="leading-tight">
              <div className="text-sm font-extrabold text-[var(--color-text-primary)]">
                Upstream
              </div>
              <div className="text-[10px] font-semibold text-[var(--color-text-muted)]">
                上游控制台
              </div>
            </div>
          </Link>

          <nav className="hidden min-w-0 flex-1 items-center gap-1 md:flex">
            {nav.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  `rounded-lg px-3 py-1.5 text-sm font-semibold transition ${
                    isActive
                      ? "bg-[var(--color-surface)] text-[var(--color-text-primary)]"
                      : "text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={toggle}
              className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] text-[var(--color-text-muted)] transition hover:text-[var(--color-text-primary)]"
              aria-label="切换明暗主题"
              title="明暗"
            >
              {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
            </button>
            <div className="hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] sm:block">
              本地脚手架
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 md:px-6 md:py-8">
        {children}
      </main>
    </div>
  );
}
