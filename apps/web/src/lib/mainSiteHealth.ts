import type { AdminSite, Channel } from "./types";

export type MainSiteChannels = {
  site: AdminSite;
  channels: Channel[];
  error?: string;
};

export function retainLastSuccessfulMainSiteChannels(
  previous: MainSiteChannels[],
  refreshed: MainSiteChannels[],
): MainSiteChannels[] {
  const previousBySiteId = new Map(
    previous.map((row) => [row.site.id, row.channels]),
  );
  return refreshed.map((row) =>
    row.error && previousBySiteId.has(row.site.id)
      ? { ...row, channels: previousBySiteId.get(row.site.id) || [] }
      : row,
  );
}
