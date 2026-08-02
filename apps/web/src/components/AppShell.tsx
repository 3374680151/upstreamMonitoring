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
    <div className="min-h-full text-ink">
      <a
        href="#main-content"
        className="sr-only z-[100] rounded-[var(--radius-sm)] bg-panel px-3 py-2 text-[13px] font-semibold text-ink-strong shadow-[var(--shadow-pop)] focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
      >
        跳转到主要内容
      </a>
      <header className="sticky top-0 z-40 border-b border-line bg-page/85 backdrop-blur-md">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-4 px-4 py-3 md:flex-nowrap md:px-6">
          <Link
            to="/"
            className="group flex shrink-0 items-center gap-2.5"
            aria-label="返回总览"
          >
            <span
              className="inline-flex h-8 w-8 items-center justify-center rounded-[8px] font-serif text-[15px] font-semibold text-ink-on-accent shadow-[var(--shadow-pop)] transition-transform duration-[var(--motion-base)] group-hover:rotate-[-3deg]"
              style={{
                backgroundImage:
                  "linear-gradient(135deg, #2c8a5a 0%, #1f6e47 100%)",
              }}
            >
              U
            </span>
            <div className="leading-tight">
              <div className="font-serif text-[15px] font-semibold tracking-[-0.01em] text-ink-strong">
                Upstream
              </div>
              <div className="t-micro mt-0.5">上游倍率监控</div>
            </div>
          </Link>

          <nav
            aria-label="主导航"
            className="hidden min-w-0 items-center gap-0.5 md:flex"
          >
            {nav.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    `inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-sm)] px-2.5 text-[13px] font-medium outline-none transition-[background-color,color] duration-[var(--motion-base)] ${
                      isActive
                        ? "bg-panel text-ink-strong shadow-[var(--shadow-hairline)]"
                        : "text-ink-muted hover:bg-panel-soft hover:text-ink-strong"
                    }`
                  }
                >
                  <Icon size={14} strokeWidth={1.7} aria-hidden />
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
                className="ml-1 h-8 rounded-[var(--radius-sm)] px-2.5 text-[13px] font-medium text-ink-muted transition-colors duration-[var(--motion-fast)] hover:text-ink-strong"
                title="点击复制微信号"
              >
                {copied ? "已复制" : `微信 ${WECHAT_CONTACT}`}
              </button>
            ) : null}
          </nav>

          <button
            type="button"
            className="ml-auto inline-flex h-8 w-8 items-center justify-center rounded-[var(--radius-sm)] border border-line bg-panel text-ink-muted transition-[border-color,color] duration-[var(--motion-fast)] hover:border-line-strong hover:text-ink-strong md:hidden"
            aria-label={mobileNavOpen ? "关闭主导航" : "打开主导航"}
            aria-expanded={mobileNavOpen}
            onClick={() => setMobileNavOpen((open) => !open)}
          >
            {mobileNavOpen ? <X size={16} /> : <Menu size={16} />}
          </button>

          <div className="ml-auto hidden items-center gap-2 md:flex">
            <div className="flex h-8 items-center gap-2 rounded-[var(--radius-sm)] border border-line bg-panel px-2.5 text-[12.5px] font-medium text-ink-muted">
              <span
                className="pulse-dot inline-block h-1.5 w-1.5 rounded-full text-accent"
                style={{ backgroundColor: "var(--color-accent)" }}
                aria-hidden
              />
              <span>渠道</span>
              <span className="tabular text-ink-strong">{siteCount}</span>
            </div>
            {actions}
            <button
              type="button"
              onClick={toggle}
              className="inline-flex h-8 w-8 items-center justify-center rounded-[var(--radius-sm)] border border-line bg-panel text-ink-muted transition-[border-color,color] duration-[var(--motion-fast)] hover:border-line-strong hover:text-ink-strong"
              aria-label="切换明暗主题"
              title="明暗"
            >
              {theme === "light" ? <Moon size={15} /> : <Sun size={15} />}
            </button>
            {onLogout ? (
              <button
                type="button"
                onClick={onLogout}
                className="inline-flex h-8 w-8 items-center justify-center rounded-[var(--radius-sm)] border border-line bg-panel text-ink-muted transition-[border-color,color] duration-[var(--motion-fast)] hover:border-danger-fg/40 hover:text-danger-fg"
                aria-label="退出登录"
                title="退出登录"
              >
                <LogOut size={15} />
              </button>
            ) : null}
          </div>

          {mobileNavOpen ? (
            <nav
              aria-label="移动端主导航"
              className="order-3 grid w-full grid-cols-2 gap-1 rounded-[var(--radius-md)] border border-line bg-panel-soft p-1.5 md:hidden"
            >
              {nav.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) =>
                      `inline-flex min-h-9 items-center gap-2 rounded-[var(--radius-sm)] px-3 py-1.5 text-[13px] font-medium outline-none transition-[background-color,color] duration-[var(--motion-base)] ${
                        isActive
                          ? "bg-panel text-ink-strong shadow-[var(--shadow-hairline)]"
                          : "text-ink-muted hover:bg-panel hover:text-ink-strong"
                      }`
                    }
                  >
                    <Icon size={15} strokeWidth={1.7} aria-hidden />
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
        className="mx-auto min-h-[calc(100dvh-4rem)] max-w-[1500px] px-4 py-6 md:px-6 md:py-8"
      >
        {children}
      </main>
    </div>
  );
}
