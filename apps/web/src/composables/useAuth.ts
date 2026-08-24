/**
 * 控制台鉴权 — 对应后端 routers/auth.py。
 * 模块级单例：authReady / authRequired / authed 三态。
 * 401 由 client.ts 广播 console-unauthorized 事件，此处监听并切回登录页。
 */
import { shallowRef } from "vue";
import { api, setConsoleToken } from "@/lib/api";
import { useToast } from "./useToast";

const authReady = shallowRef(false);
const authRequired = shallowRef(false);
const authed = shallowRef(false);
let authChecked = false;

// 401 监听：模块加载时注册一次
if (typeof window !== "undefined") {
  window.addEventListener("console-unauthorized", () => {
    authed.value = false;
    authRequired.value = true;
  });
}

export function useAuth() {
  const toast = useToast();

  if (!authChecked) {
    authChecked = true;
    api
      .authStatus()
      .then((s) => {
        authRequired.value = !!s.auth_required;
        authed.value = !!s.authenticated;
      })
      .catch(() => {
        authRequired.value = false;
        authed.value = true;
      })
      .finally(() => {
        authReady.value = true;
      });
  }

  async function handleLogout() {
    try {
      await api.logout();
      toast.success("已退出登录");
    } catch {
      toast.info("已在本机退出（服务端未确认）");
    }
    setConsoleToken("");
    authed.value = false;
    authRequired.value = true;
  }

  function setAuthed(v: boolean) {
    authed.value = v;
  }

  return { authReady, authRequired, authed, setAuthed, handleLogout };
}
