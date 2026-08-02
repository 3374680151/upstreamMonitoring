export function claimAutomaticRefresh(
  claimedSiteIds: Set<number>,
  siteId: number,
): boolean {
  if (claimedSiteIds.has(siteId)) return false;
  claimedSiteIds.add(siteId);
  return true;
}
