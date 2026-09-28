import type { Source, SourceHealth } from "../../api/types";
import { ageMs } from "../../components/format";

/** C5: expected traffic but no event for more than 60 s is a silent source. */
export const SILENT_AFTER_MS = 60_000;

export interface SourceRow {
  source: Source;
  health: SourceHealth | undefined;
  /** How long the source has been silent, or null when it is not. */
  silentMs: number | null;
}

/** Silence counts from the last event, or from registration if nothing ever arrived. */
export function silentFor(source: Source, health: SourceHealth | undefined, now: number): number | null {
  const expected = health?.expected_eps ?? source.expected_eps;
  if (expected <= 0 || source.status !== "active") return null;
  const quiet = ageMs(health?.last_seen ?? source.created_at, now);
  return quiet !== null && quiet > SILENT_AFTER_MS ? quiet : null;
}

/** Registry rows joined with health by id. Without health data nothing is flagged: unknown is not silent. */
export function buildRows(
  sources: readonly Source[],
  health: readonly SourceHealth[] | undefined,
  now: number,
): SourceRow[] {
  const byId = new Map((health ?? []).map((h) => [h.source_id, h]));
  return sources.map((source) => {
    const row = byId.get(source.id);
    return { source, health: row, silentMs: health ? silentFor(source, row, now) : null };
  });
}

export function epsDelta(actual: number, expected: number): string | null {
  if (expected <= 0) return null;
  const pct = Math.round(((actual - expected) / expected) * 100);
  if (pct === 0) return "±0%";
  return pct > 0 ? `+${pct}%` : `${pct}%`;
}

export function formatSkew(ms: number | null | undefined): string {
  return ms === null || ms === undefined ? "—" : `${ms} ms`;
}
