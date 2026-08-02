export function parseChannelPriority(value: string): number | null {
  const normalized = value.trim();
  if (!/^-?\d+$/.test(normalized)) return null;
  const priority = Number(normalized);
  return Number.isSafeInteger(priority) ? priority : null;
}
