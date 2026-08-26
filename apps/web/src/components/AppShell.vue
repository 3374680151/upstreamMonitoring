<script setup lang="ts">
import { shallowRef, watch } from "vue";
import { useRoute, RouterLink } from "vue-router";
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
} from "lucide-vue-next";
import { useTheme } from "@/composables/useTheme";
import { useToast } from "@/composables/useToast";

interface Props {
  siteCount?: number;
  onLogout?: () => void;
}
const props = withDefaults(defineProps<Props>(), { siteCount: 0 });

const WECHAT_CONTACT =
  (import.meta.env.VITE_CONTACT_WECHAT as string | undefined)?.trim() || "";

const nav = [
  { to: "/", label: "总览", exact: true, icon: LayoutDashboard },
  { to: "/channels", label: "主站监控", exact: false, icon: MonitorCog },
  { to: "/sites", label: "渠道监控", exact: false, icon: Radar },
  { to: "/detail", label: "渠道详情", exact: false, icon: PanelTop },
  { to: "/changes", label: "变化记录", exact: false, icon: List },
  { to: "/balance", label: "余额", exact: false, icon: WalletCards },
  { to: "/notifications", label: "消息推送", exact: false, icon: BellRing },
];

const { theme, toggle } = useTheme();
const route = useRoute();
const toast = useToast();
const mobileNavOpen = shallowRef(false);
const copied = shallowRef(false);

watch(
  () => route.path,
  () => {
    mobileNavOpen.value = false;
  },
);

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

async function handleCopyWechat() {
  if (!WECHAT_CONTACT) return;
  try {
    await copyText(WECHAT_CONTACT);
    copied.value = true;
    window.setTimeout(() => (copied.value = false), 1600);
    toast.success("微信号已复制");
  } catch {
    toast.error(`复制失败，微信号：${WECHAT_CONTACT}`);
  }
}
</script>

<template>
  <div class="min-h-full text-ink">
    <a
      href="#main-content"
      class="sr-only z-[100] rounded-[var(--radius-sm)] bg-panel px-3 py-2 text-[13px] font-semibold text-ink-strong shadow-[var(--shadow-pop)] focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
    >
      跳转到主要内容
    </a>
    <header class="sticky top-0 z-40 border-b border-line bg-page/85 backdrop-blur-md">
      <div class="mx-auto flex max-w-[1500px] flex-wrap items-center gap-4 px-4 py-3 md:flex-nowrap md:px-6">
        <RouterLink to="/" class="group flex shrink-0 items-center gap-2.5" aria-label="返回总览">
          <span
            class="inline-flex h-8 w-8 items-center justify-center rounded-[8px] font-serif text-[15px] font-semibold text-ink-on-accent shadow-[var(--shadow-pop)] transition-transform duration-[var(--motion-base)] group-hover:rotate-[-3deg]"
            style="background-image: linear-gradient(135deg, #2c8a5a 0%, #1f6e47 100%)"
          >
            U
          </span>
          <div class="leading-tight">
            <div class="font-serif text-[15px] font-semibold tracking-[-0.01em] text-ink-strong">
              Upstream
            </div>
            <div class="t-micro mt-0.5">上游倍率监控</div>
          </div>
        </RouterLink>

        <nav aria-label="主导航" class="hidden min-w-0 items-center gap-0.5 md:flex">
          <RouterLink
            v-for="item in nav"
            :key="item.to"
            :to="item.to"
            custom
            v-slot="{ href, isActive, isExactActive, navigate }"
          >
            <a
              :href="href"
              :class="[
                'inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-sm)] px-2.5 text-[13px] font-medium outline-none transition-[background-color,color] duration-[var(--motion-base)]',
                item.exact
                  ? isExactActive
                    ? 'bg-panel text-ink-strong shadow-[var(--shadow-hairline)]'
                    : 'text-ink-muted hover:bg-panel-soft hover:text-ink-strong'
                  : isActive
                    ? 'bg-panel text-ink-strong shadow-[var(--shadow-hairline)]'
                    : 'text-ink-muted hover:bg-panel-soft hover:text-ink-strong',
              ]"
              @click="navigate"
            >
              <component :is="item.icon" :size="14" :stroke-width="1.7" />
              {{ item.label }}
            </a>
          </RouterLink>
          <button
            v-if="WECHAT_CONTACT"
            type="button"
            class="ml-1 h-8 rounded-[var(--radius-sm)] px-2.5 text-[13px] font-medium text-ink-muted transition-colors duration-[var(--motion-fast)] hover:text-ink-strong"
            title="点击复制微信号"
            @click="handleCopyWechat"
          >
            {{ copied ? "已复制" : `微信 ${WECHAT_CONTACT}` }}
          </button>
        </nav>

        <button
          type="button"
          class="ml-auto inline-flex h-8 w-8 items-center justify-center rounded-[var(--radius-sm)] border border-line bg-panel text-ink-muted transition-[border-color,color] duration-[var(--motion-fast)] hover:border-line-strong hover:text-ink-strong md:hidden"
          :aria-label="mobileNavOpen ? '关闭主导航' : '打开主导航'"
          :aria-expanded="mobileNavOpen"
          @click="mobileNavOpen = !mobileNavOpen"
        >
          <X v-if="mobileNavOpen" :size="16" />
          <Menu v-else :size="16" />
        </button>

        <div class="ml-auto hidden items-center gap-2 md:flex">
          <div class="flex h-8 items-center gap-2 rounded-[var(--radius-sm)] border border-line bg-panel px-2.5 text-[12.5px] font-medium text-ink-muted">
            <span
              class="pulse-dot inline-block h-1.5 w-1.5 rounded-full"
              style="background-color: var(--color-accent)"
              aria-hidden="true"
            />
            <span>渠道</span>
            <span class="tabular text-ink-strong">{{ props.siteCount }}</span>
          </div>
          <slot name="actions" />
          <button
            type="button"
            class="inline-flex h-8 w-8 items-center justify-center rounded-[var(--radius-sm)] border border-line bg-panel text-ink-muted transition-[border-color,color] duration-[var(--motion-fast)] hover:border-line-strong hover:text-ink-strong"
            aria-label="切换明暗主题"
            title="明暗"
            @click="toggle"
          >
            <Moon v-if="theme === 'light'" :size="15" />
            <Sun v-else :size="15" />
          </button>
          <button
            v-if="onLogout"
            type="button"
            class="inline-flex h-8 w-8 items-center justify-center rounded-[var(--radius-sm)] border border-line bg-panel text-ink-muted transition-[border-color,color] duration-[var(--motion-fast)] hover:border-danger-fg/40 hover:text-danger-fg"
            aria-label="退出登录"
            title="退出登录"
            @click="onLogout"
          >
            <LogOut :size="15" />
          </button>
        </div>

        <nav
          v-if="mobileNavOpen"
          aria-label="移动端主导航"
          class="order-3 grid w-full grid-cols-2 gap-1 rounded-[var(--radius-md)] border border-line bg-panel-soft p-1.5 md:hidden"
        >
          <RouterLink
            v-for="item in nav"
            :key="item.to"
            :to="item.to"
            custom
            v-slot="{ href, isActive, isExactActive, navigate }"
          >
            <a
              :href="href"
              :class="[
                'inline-flex min-h-9 items-center gap-2 rounded-[var(--radius-sm)] px-3 py-1.5 text-[13px] font-medium outline-none transition-[background-color,color] duration-[var(--motion-base)]',
                item.exact
                  ? isExactActive
                    ? 'bg-panel text-ink-strong shadow-[var(--shadow-hairline)]'
                    : 'text-ink-muted hover:bg-panel hover:text-ink-strong'
                  : isActive
                    ? 'bg-panel text-ink-strong shadow-[var(--shadow-hairline)]'
                    : 'text-ink-muted hover:bg-panel hover:text-ink-strong',
              ]"
              @click="navigate"
            >
              <component :is="item.icon" :size="15" :stroke-width="1.7" />
              {{ item.label }}
            </a>
          </RouterLink>
        </nav>
      </div>
    </header>

    <main
      id="main-content"
      class="mx-auto min-h-[calc(100dvh-4rem)] max-w-[1500px] px-4 py-6 md:px-6 md:py-8"
    >
      <slot />
    </main>
  </div>
</template>
