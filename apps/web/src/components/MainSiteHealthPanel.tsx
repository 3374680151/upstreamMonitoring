import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Activity, AlertTriangle, CheckCircle2, PauseCircle } from "lucide-react";
import { Panel } from "@/components/Panel";
import { StatCard } from "@/components/StatCard";
import { Button } from "@/components/ui";
import { errorText, useToast } from "@/components/Toast";
import { api } from "@/lib/api";
import {
  retainLastSuccessfulMainSiteChannels,
  type MainSiteChannels,
} from "@/lib/mainSiteHealth";
import { normalizedChannelStatus } from "@/lib/sub2apiChannel";
import type { Channel } from "@/lib/types";

/**
 * 主站健康总览：聚合所有 NewAPI / sub2api 主站下渠道的状态。
 * 只在挂载时拉一次（不跟随总览页 15s 轮询），避免频繁打上游管理接口。
 */
export function MainSiteHealthPanel() {
  const toast = useToast();
  const [rows, setRows] = useState<MainSiteChannels[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  // 首次自动加载不弹提示（避免进总览就冒泡），只有用户点「刷新」才反馈
  const notifyRef = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const resp = await api.adminSites();
      const sites = resp.data || [];
      if (!sites.length) {
        setRows([]);
        return;
      }
      const loaded = await Promise.all(
        sites.map(async (site) => {
          try {
            const chResp = await api.channels(site.id);
            return { site, channels: chResp.data || [] };
          } catch (err) {
            return {
              site,
              channels: [],
              error: err instanceof Error ? err.message : String(err),
            };
          }
        }),
      );
      setRows((previous) =>
        retainLastSuccessfulMainSiteChannels(previous, loaded),
      );
      const failed = loaded.filter((r) => r.error);
      if (notifyRef.current) {
        if (failed.length) {
          toast.error(`${failed.length} 个主站的渠道读取失败：${failed[0].error}`);
        } else {
          const total = loaded.reduce((n, r) => n + r.channels.length, 0);
          toast.success(`已刷新：${loaded.length} 个主站 · ${total} 个渠道`);
        }
      }
    } catch (err) {
      const message = errorText(err, "读取主站失败");
      setError(message);
      if (notifyRef.current) toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const stats = useMemo(() => {
    const acc = { total: 0, active: 0, disabled: 0, error: 0 };
    for (const row of rows) {
      for (const ch of row.channels) {
        acc.total += 1;
        const status = normalizedChannelStatus(ch);
        if (status === "active") acc.active += 1;
        else if (status === "disabled") acc.disabled += 1;
        else acc.error += 1;
      }
    }
    return acc;
  }, [rows]);

  const recoverableNewApiErrors = useMemo(
    () =>
      rows.flatMap((row) =>
        row.site.platform === "newapi"
          ? row.channels
              .filter((ch) => Number(ch.status) === 3)
              .map((ch) => ({ ch, site: row.site }))
          : [],
      ),
    [rows],
  );

  const failedSites = useMemo(() => rows.filter((r) => r.error), [rows]);

  async function reEnable(siteId: number, ch: Channel) {
    const label = ch.name || `#${ch.id}`;
    const actionKey = `${siteId}:${ch.id}`;
    setBusyKey(actionKey);
    try {
      const resp = await api.updateChannel(siteId, ch.id, { status: 1 });
      if (!resp.success) throw new Error(resp.message || "重新启用失败");
      await load();
      toast.success(`已重新启用渠道「${label}」`);
    } catch (err) {
      const message = errorText(err, `重新启用「${label}」失败`);
      setError(message);
      toast.error(message);
    } finally {
      setBusyKey((current) => (current === actionKey ? null : current));
    }
  }

  const siteCountLabel = rows.length
    ? `${rows.length} 个主站`
    : "未配置主站";

  return (
    <Panel
      title="主站健康"
      subtitle={`NewAPI / sub2api 主站渠道状态 · ${siteCountLabel}`}
      action={
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            loading={loading}
            onClick={() => {
              notifyRef.current = true;
              load();
            }}
          >
            刷新
          </Button>
          <Link
            to="/channels"
            className="rounded-[var(--radius-sm)] px-2.5 py-1 text-[13px] font-medium text-accent transition-colors duration-[var(--motion-fast)] hover:text-accent-hover hover:underline"
          >
            去管理 →
          </Link>
        </div>
      }
    >
      {error ? (
        <div className="mb-3 rounded-[var(--radius-sm)] border border-danger-fg/30 bg-danger-bg px-3 py-2 text-[13px] text-danger-fg">
          {error}
        </div>
      ) : null}

      {!loading && !rows.length ? (
        <div className="py-8 text-center text-[13px] text-ink-muted">
          还没有配置主站。到「
          <Link to="/channels" className="text-accent hover:underline">
            主站监控
          </Link>
          」添加 NewAPI 或 sub2api 主站。
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard
              label="渠道总数"
              value={stats.total}
              hint={siteCountLabel}
              icon={<Activity size={17} />}
              tone="brand"
            />
            <StatCard
              label="运行中"
              value={stats.active}
              hint="状态正常的渠道"
              icon={<CheckCircle2 size={17} />}
              tone="info"
            />
            <StatCard
              label="已停用"
              value={stats.disabled}
              hint="主站中已关闭"
              icon={<PauseCircle size={17} />}
              tone="warning"
            />
            <StatCard
              label="异常"
              value={stats.error}
              hint={stats.error ? "需要检查渠道配置" : "无异常渠道"}
              icon={<AlertTriangle size={17} />}
              tone={stats.error ? "danger" : "neutral"}
            />
          </div>

          {recoverableNewApiErrors.length ? (
            <div className="mt-3 rounded-[var(--radius-md)] border border-danger-fg/30 bg-danger-bg px-4 py-3 text-[13px]">
              <div className="mb-1.5 flex items-center gap-1.5 font-semibold text-danger-fg">
                <AlertTriangle size={13} />
                {recoverableNewApiErrors.length} 个 NewAPI 渠道被自动停用，请排查
              </div>
              <div className="flex flex-wrap gap-1.5">
                {recoverableNewApiErrors.map(({ ch, site }) => (
                  <span
                    key={`${site.id}-${ch.id}`}
                    className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-line bg-panel px-2 py-1 text-[12px]"
                  >
                    <span className="font-semibold text-ink-strong">
                      {ch.name || `#${ch.id}`}
                    </span>
                    {rows.length > 1 ? (
                      <span className="text-ink-soft">{site.name}</span>
                    ) : null}
                    <button
                      className="text-accent transition-opacity duration-[var(--motion-fast)] hover:opacity-80 disabled:opacity-50"
                      disabled={busyKey === `${site.id}:${ch.id}`}
                      onClick={() => reEnable(site.id, ch)}
                    >
                      重新启用
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          {failedSites.length ? (
            <div className="mt-3 rounded-[var(--radius-md)] border border-warning-fg/25 bg-warning-bg px-4 py-3 text-[12.5px] text-warning-fg">
              {failedSites.map((r) => (
                <div key={r.site.id}>
                  主站「{r.site.name}」渠道读取失败：{r.error}
                </div>
              ))}
            </div>
          ) : null}
        </>
      )}
    </Panel>
  );
}
