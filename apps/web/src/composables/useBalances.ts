/**
 * 账户额度按需查询 — NewAPI /api/user/self、sub2api /api/v1/auth/me。
 * 每个使用方独立实例（非单例）：rows / busy 不跨页面共享。
 */
import { shallowRef, computed, type Ref } from "vue";
import { api } from "@/lib/api";
import { errorText, useToast } from "./useToast";
import type { Site, SiteAccount } from "@/lib/types";

export type BalanceRow =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "ok"; account: SiteAccount; fetchedAt?: string }
  | { state: "error"; message: string };

export function usd(value?: number | null): string {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return "—";
  }
  return `$${Number(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function useBalances(sites: Ref<Site[]>) {
  const toast = useToast();
  const rows = shallowRef<Record<number, BalanceRow>>({});
  const busy = shallowRef(false);

  async function queryOne(site: Site, notify = true): Promise<boolean> {
    const next = { ...rows.value, [site.id]: { state: "loading" } as BalanceRow };
    rows.value = next;
    try {
      const resp = await api.siteAccount(site.id);
      if (!resp.account) throw new Error(resp.message || "未返回账户信息");
      rows.value = {
        ...rows.value,
        [site.id]: { state: "ok", account: resp.account!, fetchedAt: resp.fetched_at },
      };
      if (notify) toast.success(`「${site.name}」余额 ${usd(resp.account.balance_usd)}`);
      return true;
    } catch (err) {
      const message = errorText(err, "读取失败");
      rows.value = { ...rows.value, [site.id]: { state: "error", message } };
      if (notify) toast.error(`「${site.name}」查询失败：${message}`);
      return false;
    }
  }

  async function queryAll(): Promise<void> {
    if (!sites.value.length) {
      toast.info("还没有配置渠道站点");
      return;
    }
    busy.value = true;
    try {
      const results: boolean[] = [];
      for (const site of sites.value) {
        results.push(await queryOne(site, false));
      }
      const ok = results.filter(Boolean).length;
      const failed = results.length - ok;
      if (!failed) toast.success(`已查询 ${ok} 个渠道的余额`);
      else if (!ok) toast.error(`${failed} 个渠道全部查询失败，见列表内原因`);
      else toast.info(`查询完成：成功 ${ok} 个，失败 ${failed} 个（原因见列表）`);
    } finally {
      busy.value = false;
    }
  }

  const summary = computed(() => {
    let total = 0;
    let okCount = 0;
    let errCount = 0;
    for (const site of sites.value) {
      const row = rows.value[site.id];
      if (!row) continue;
      if (row.state === "ok") {
        okCount += 1;
        const v = Number(row.account.balance_usd);
        if (Number.isFinite(v)) total += v;
      } else if (row.state === "error") {
        errCount += 1;
      }
    }
    return { total, okCount, errCount, queried: okCount + errCount };
  });

  return { rows, busy, queryOne, queryAll, summary };
}
