import { normalizeOrigin } from "./sub2api.js";

export function selectExistingTargetTab(tabs, targetOrigin) {
  return (
    (Array.isArray(tabs) ? tabs : []).find(
      (tab) =>
        tab?.id &&
        normalizeOrigin(tab.url || tab.pendingUrl || "") === targetOrigin,
    ) || null
  );
}
