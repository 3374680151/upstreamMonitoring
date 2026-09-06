/**
 * 主站渠道最近请求首字延迟 — 对应后端 routers/admin_sites.py
 * GET /admin/sites/{id}/channels/recent-requests（services/channel_logs_service.py）。
 * 后端自带 SWR 短缓存；这里不轮询：进页面/切主站随页面 load() 拉一次，工具栏按钮 refresh=true 强制穿透。
 */
import { ref, shallowRef } from "vue";
import { api } from "@/lib/api";
import { errorText } from "@/composables/useToast";
import type { ChannelRecentRequestEntry, TtftThresholds } from "@/lib/types";

export function useChannelRecentRequests() {
  const entries = shallowRef<Record<string, ChannelRecentRequestEntry>>({});
  const thresholds = shallowRef<TtftThresholds | null>(null);
  const loading = ref(false);
  const error = ref("");
  let loadVersion = 0;
  let loadedSiteId: number | null = null;

  async function load(
    adminSiteId: number,
    channelIds: number[],
    refresh = false,
  ): Promise<boolean> {
    if (!adminSiteId || channelIds.length === 0) return false;
    if (loadedSiteId !== adminSiteId) {
      // 切主站先清空：不同主站的渠道 id 会重叠，不清理会把上一主站的色点
      // 串到新主站表格（新数据受限速门影响可能数秒后才回来）。
      loadedSiteId = adminSiteId;
      entries.value = {};
      thresholds.value = null;
      error.value = "";
    }
    const version = ++loadVersion;
    loading.value = true;
    try {
      const response = await api.channelRecentRequests(
        adminSiteId,
        channelIds,
        refresh,
      );
      if (version !== loadVersion) return false;
      entries.value = response.data?.channels || {};
      thresholds.value = response.data?.thresholds || null;
      error.value = "";
      return true;
    } catch (err) {
      if (version !== loadVersion) return false;
      error.value = errorText(err, "最近请求延迟加载失败");
      return false;
    } finally {
      if (version === loadVersion) loading.value = false;
    }
  }

  return { entries, thresholds, loading, error, load };
}
