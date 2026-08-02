import { useCallback, useMemo, useState } from "react";
import { errorText, useToast } from "@/components/Toast";
import { api } from "@/lib/api";
import type { Site, SiteAccount } from "@/lib/types";

/** 单个渠道站点的余额查询状态 */
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

/**
 * 按「你配置的渠道站点」查余额：NewAPI 走 /api/user/self，sub2api 走 /api/v1/auth/me。
 *
 * 一律**点按钮才查**——每个站点都要拿登录态请求上游，sub2api 密码模式还会真登录一次，
 * 进页面自动查会频繁打上游。总览卡片与「余额」页共用本 hook，避免两份逻辑走偏。
 */
export function useBalances(sites: Site[]) {
  const toast = useToast();
  const [rows, setRows] = useState<Record<number, BalanceRow>>({});
  const [busy, setBusy] = useState(false);

  /** 查单个站点。notify=false 时不单独弹提示，交给批量调用方汇总。 */
  const queryOne = useCallback(
    async (site: Site, notify = true): Promise<boolean> => {
      setRows((p) => ({ ...p, [site.id]: { state: "loading" } }));
      try {
        const resp = await api.siteAccount(site.id);
        if (!resp.account) throw new Error(resp.message || "未返回账户信息");
        setRows((p) => ({
          ...p,
          [site.id]: { state: "ok", account: resp.account!, fetchedAt: resp.fetched_at },
        }));
        if (notify) toast.success(`「${site.name}」余额 ${usd(resp.account.balance_usd)}`);
        return true;
      } catch (err) {
        const message = errorText(err, "读取失败");
        setRows((p) => ({ ...p, [site.id]: { state: "error", message } }));
        if (notify) toast.error(`「${site.name}」查询失败：${message}`);
        return false;
      }
    },
    [toast],
  );

  /** 一键查询全部：逐个拉取，成败都必定给一条汇总提示 */
  const queryAll = useCallback(async () => {
    if (!sites.length) {
      toast.info("还没有配置渠道站点");
      return;
    }
    setBusy(true);
    try {
      const results: boolean[] = [];
      for (const site of sites) {
        results.push(await queryOne(site, false));
      }
      const ok = results.filter(Boolean).length;
      const failed = results.length - ok;
      if (!failed) toast.success(`已查询 ${ok} 个渠道的余额`);
      else if (!ok) toast.error(`${failed} 个渠道全部查询失败，见列表内原因`);
      else toast.info(`查询完成：成功 ${ok} 个，失败 ${failed} 个（原因见列表）`);
    } finally {
      setBusy(false);
    }
  }, [sites, queryOne, toast]);

  const summary = useMemo(() => {
    let total = 0;
    let okCount = 0;
    let errCount = 0;
    for (const site of sites) {
      const row = rows[site.id];
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
  }, [rows, sites]);

  return { rows, busy, queryOne, queryAll, summary };
}
