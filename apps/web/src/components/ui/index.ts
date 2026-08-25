/**
 * UI 基础组件统一出口。
 * 页面/组件从此处导入 Button / Input / Modal / ConfirmDialog 等。
 */
export { default as Button } from "./Button.vue";
export { default as Spinner } from "./Spinner.vue";
export { default as Input } from "./Input.vue";
export { default as Textarea } from "./Textarea.vue";
export { default as Select } from "./Select.vue";
export { default as Tabs } from "./Tabs.vue";
export { default as Field } from "./Field.vue";
export { default as SwitchRow } from "./SwitchRow.vue";
export { default as Modal } from "./Modal.vue";
export { default as ConfirmDialog } from "./ConfirmDialog.vue";
export { default as EmptyState } from "./EmptyState.vue";

/** 颜色变体收敛到 token，避免组件分散拼 var(--color-…) */
export const colorTokens = {
  page: "var(--color-page)",
  paper: "var(--color-paper)",
  panel: "var(--color-panel)",
  panelSoft: "var(--color-panel-soft)",
  sunken: "var(--color-sunken)",
  sunkenHover: "var(--color-sunken-hover)",
  sunkenActive: "var(--color-sunken-active)",
  overlay: "var(--color-overlay)",
  ink: "var(--color-ink)",
  inkStrong: "var(--color-ink-strong)",
  inkMuted: "var(--color-ink-muted)",
  inkSoft: "var(--color-ink-soft)",
  inkFaint: "var(--color-ink-faint)",
  inkOnAccent: "var(--color-ink-on-accent)",
  line: "var(--color-line)",
  lineSoft: "var(--color-line-soft)",
  lineStrong: "var(--color-line-strong)",
  accent: "var(--color-accent)",
  accentHover: "var(--color-accent-hover)",
  accentSoft: "var(--color-accent-soft)",
  accentRing: "var(--color-accent-ring)",
  successFg: "var(--color-success-fg)",
  successBg: "var(--color-success-bg)",
  warningFg: "var(--color-warning-fg)",
  warningBg: "var(--color-warning-bg)",
  infoFg: "var(--color-info-fg)",
  infoBg: "var(--color-info-bg)",
  dangerFg: "var(--color-danger-fg)",
  dangerBg: "var(--color-danger-bg)",
} as const;
