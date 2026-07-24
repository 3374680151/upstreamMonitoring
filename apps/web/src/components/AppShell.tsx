import { Link, NavLink } from "react-router-dom";
import { Moon, Sun } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";

const COMMUNITY_GROUP = "259844673";

const nav = [
  { to: "/", label: "总览", end: true },
  { to: "/sites", label: "站点监控" },
  { to: "/detail", label: "站点详情" },
  { to: "/changes", label: "变化记录" },
  { to: "/notifications", label: "消息推送" },
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

async function copyText(text: string) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const input = document.createElement("input");
  input.value = text;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.appendChild(input);
  input.select();
  document.execCommand("copy");
  input.remove();
}

export function AppShell({
  children,
  actions,
  siteCount = 0,
}: {
  children: ReactNode;
  actions?: ReactNode;
  siteCount?: number;
}) {
  const { theme, toggle } = useTheme();
  const [copied, setCopied] = useState(false);

  return (
    <div className="min-h-full bg-[var(--color-page)] text-[var(--color-text-body)]">
      <header className="sticky top-0 z-40 border-b border-[var(--color-border)] bg-[var(--color-page-translucent)] backdrop-blur-sm">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-3 px-4 py-3 md:px-6">
          <Link to="/" className="flex shrink-0 items-center gap-2">
            <span className="inline-flex h-8 w-8 items-center justify-center rounded-xl bg-[var(--color-brand)] text-sm font-black text-[var(--color-text-on-primary)]">
              U
            </span>
            <div className="leading-tight">
              <div className="text-sm font-extrabold text-[var(--color-text-primary)]">
                Upstream
              </div>
              <div className="text-[10px] font-semibold text-[var(--color-text-muted)]">
                上游倍率监控
              </div>
            </div>
          </Link>

          <nav className="order-3 flex w-full min-w-0 flex-wrap items-center gap-1 md:order-none md:w-auto md:flex-1">
            {nav.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
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
            <button
              type="button"
              onClick={async () => {
                try {
                  await copyText(COMMUNITY_GROUP);
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1600);
                } catch {
                  alert(`QQ群：${COMMUNITY_GROUP}`);
                }
              }}
              className="rounded-lg px-3 py-1.5 text-sm font-semibold text-[var(--color-text-muted)] transition hover:text-[var(--color-text-primary)]"
              title="点击复制 QQ 群号"
            >
              {copied ? "已复制群号" : `交流群 ${COMMUNITY_GROUP}`}
            </button>
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <div className="hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] sm:block">
              监控 {siteCount}
            </div>
            {actions}
            <button
              type="button"
              onClick={toggle}
              className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] text-[var(--color-text-muted)] transition hover:text-[var(--color-text-primary)]"
              aria-label="切换明暗主题"
              title="明暗"
            >
              {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] px-4 py-6 md:px-6 md:py-8">
        {children}
      </main>
    </div>
  );
}
