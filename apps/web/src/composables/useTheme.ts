/**
 * 明暗主题切换 — 从 AppShell.tsx 提取。
 * 读写 localStorage + 同步 html[data-theme]。
 */
import { shallowRef, watch } from "vue";

type Theme = "light" | "dark";

function readSaved(): Theme {
  try {
    const saved = localStorage.getItem("upstream-theme");
    if (saved === "dark" || saved === "light") return saved;
  } catch {
    /* private mode */
  }
  return "light";
}

const theme = shallowRef<Theme>(readSaved());

// 挂载时同步一次 DOM
if (typeof document !== "undefined") {
  document.documentElement.setAttribute("data-theme", theme.value);
}

watch(theme, (val) => {
  document.documentElement.setAttribute("data-theme", val);
  try {
    localStorage.setItem("upstream-theme", val);
  } catch {
    /* ignore */
  }
});

export function useTheme() {
  return {
    theme,
    toggle: () => {
      theme.value = theme.value === "light" ? "dark" : "light";
    },
  };
}
