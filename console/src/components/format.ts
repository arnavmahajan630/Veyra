/** Milliseconds since the epoch; NaN when unreadable. IF-ENVELOPE timestamps carry
 *  nanoseconds, which Date.parse does not promise to read, so trim them to milliseconds. */
export function parseTime(iso: string): number {
  return Date.parse(iso.replace(/(\.\d{3})\d+/, "$1"));
}

/** How long ago `iso` was, relative to `now`; null when there is no readable timestamp. */
export function ageMs(iso: string | null, now: number): number | null {
  if (!iso) return null;
  const at = parseTime(iso);
  return Number.isNaN(at) ? null : now - at;
}

/** "12s", "1m 20s", "2h 5m": ages an operator reads at a glance. */
export function formatAge(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1_000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function formatEps(eps: number): string {
  return eps >= 100 ? String(Math.round(eps)) : eps.toFixed(1).replace(/\.0$/, "");
}
