import { Link, NavLink, useLocation } from "react-router-dom";
import {
  BellRing,
  LayoutDashboard,
  List,
  LogOut,
  Menu,
  MonitorCog,
  Moon,
  PanelTop,
  Radar,
  Sun,
  WalletCards,
  X,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useToast } from "@/components/Toast";

// 联系方式改为构建期可配置：在 apps/web/.env 设 VITE_CONTACT_WECHAT。
// 默认留空则不展示该按钮，避免把个人微信号硬编码进开源仓库。
const WECHAT_CONTACT =
  (import.meta.env.VITE_CONTACT_WECHAT as string | undefined)?.trim() || "";

const nav: Array<{
  to: string;
  label: string;
  end?: boolean;
  icon: LucideIcon;
}> = [
  { to: "/", label: "总览", end: true, icon: LayoutDashboard },
  { to: "/channels", label: "主站监控", icon: MonitorCog },
  { to: "/sites", label: "渠道监控", icon: Radar },
  { to: "/detail", label: "渠道详情", icon: PanelTop },
  { to: "/changes", label: "变化记录", icon: List },
  { to: "/balance", label: "余额", icon: WalletCards },
  { to: "/notifications", label: "消息推送", icon: BellRing },
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
  onLogout,
}: {
  children: ReactNode;
  actions?: ReactNode;
  siteCount?: number;
  onLogout?: () => void;
}) {
  const { theme, toggle } = useTheme();
  const location = useLocation();
  const [copied, setCopied] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const toast = useToast();

  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname]);

  return (
    <div className="min-h-full bg-[var(--color-page)] text-[var(--color-text-body)]">
      <a
        href="#main-content"
        className="sr-only z-[100] rounded-lg bg-[var(--color-panel)] px-3 py-2 text-sm font-semibold text-[var(--color-text-primary)] shadow-[var(--shadow-control)] focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
      >
        跳转到主要内容
      </a>
      <header className="relative sticky top-0 z-40 border-b border-[var(--color-border)] bg-[var(--color-page-translucent)] backdrop-blur-sm">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-3 px-4 py-3 md:px-6">
          <Link
            to="/"
            className="group flex shrink-0 items-center gap-2.5"
            aria-label="返回总览"
          >
            <span
              className="inline-flex h-9 w-9 items-center justify-center rounded-xl text-sm font-black text-white shadow-[0_6px_16px_#45bf7855] transition duration-300 group-hover:scale-105"
              style={{
                backgroundImage:
                  "linear-gradient(135deg, #52cc86 0%, #2f9d63 100%)",
              }}
            >
              U
            </span>
            <div className="leading-tight">
              <div className="text-sm font-extrabold tracking-tight text-[var(--color-text-primary)]">
                Upstream
              </div>
              <div className="text-[10px] font-semibold text-[var(--color-text-muted)]">
                上游倍率监控
              </div>
            </div>
          </Link>

          <nav
            aria-label="主导航"
            className="order-3 hidden min-w-0 items-center gap-1 md:order-none md:flex"
          >
            {nav.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    `inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-semibold outline-none transition duration-200 focus-visible:ring-2 focus-visible:ring-[var(--color-brand)] ${
                      isActive
                        ? "bg-[var(--color-surface-raised)] text-[var(--color-text-primary)] shadow-[var(--shadow-control)] ring-1 ring-[var(--color-border-subtle)]"
                        : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface)] hover:text-[var(--color-text-primary)]"
                    }`
                  }
                >
                  <Icon size={15} strokeWidth={1.8} aria-hidden />
                  {item.label}
                </NavLink>
              );
            })}
            {WECHAT_CONTACT ? (
              <button
                type="button"
                onClick={async () => {
                  try {
                    await copyText(WECHAT_CONTACT);
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1600);
                    toast.success("微信号已复制");
                  } catch {
                    // 复制失败（非安全上下文/无剪贴板权限）时把号码摆出来，别让用户干瞪眼
                    toast.error(`复制失败，微信号：${WECHAT_CONTACT}`);
                  }
                }}
                className="rounded-lg px-3 py-1.5 text-sm font-semibold text-[var(--color-text-muted)] transition hover:text-[var(--color-text-primary)]"
                title="点击复制微信号"
              >
                {copied ? "已复制微信号" : `微信 ${WECHAT_CONTACT}`}
              </button>
            ) : null}
          </nav>

          <button
            type="button"
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] text-[var(--color-text-muted)] transition hover:border-[var(--color-border-muted)] hover:text-[var(--color-text-primary)] md:hidden"
            aria-label={mobileNavOpen ? "关闭主导航" : "打开主导航"}
            aria-expanded={mobileNavOpen}
            onClick={() => setMobileNavOpen((open) => !open)}
          >
            {mobileNavOpen ? <X size={17} /> : <Menu size={17} />}
          </button>

          <div className="ml-auto flex items-center gap-2">
            <div className="hidden items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] sm:flex">
              <span
                className="pulse-dot inline-block h-1.5 w-1.5 rounded-full text-[var(--color-brand)]"
                style={{ backgroundColor: "var(--color-brand)" }}
                aria-hidden
              />
              渠道 <span className="tabular-nums text-[var(--color-text-primary)]">{siteCount}</span>
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
            {onLogout ? (
              <button
                type="button"
                onClick={onLogout}
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] text-[var(--color-text-muted)] transition hover:text-[var(--color-danger-text)]"
                aria-label="退出登录"
                title="退出登录"
              >
                <LogOut size={16} />
              </button>
            ) : null}
          </div>

          {mobileNavOpen ? (
            <nav
              aria-label="移动端主导航"
              className="order-3 grid w-full grid-cols-2 gap-1 rounded-xl border border-[var(--color-border)] bg-[var(--color-panel-soft)] p-2 shadow-[var(--shadow-control)] md:hidden"
            >
              {nav.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) =>
                      `inline-flex min-h-10 items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold outline-none transition focus-visible:ring-2 focus-visible:ring-[var(--color-brand)] ${
                        isActive
                          ? "bg-[var(--color-surface-raised)] text-[var(--color-text-primary)] shadow-[var(--shadow-control)]"
                          : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface)] hover:text-[var(--color-text-primary)]"
                      }`
                    }
                  >
                    <Icon size={16} strokeWidth={1.8} aria-hidden />
                    {item.label}
                  </NavLink>
                );
              })}
            </nav>
          ) : null}
        </div>
      </header>

      <main
        id="main-content"
        className="mx-auto min-h-[calc(100dvh-4.5rem)] max-w-[1500px] px-4 py-6 md:px-6 md:py-8"
      >
        {children}
      </main>
    </div>
  );
}
