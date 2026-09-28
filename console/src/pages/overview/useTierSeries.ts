import { useEffect, useMemo, useSyncExternalStore } from "react";
import type { Overview, TierCounts } from "../../api/types";

/** 15 minutes of 1 s ticks (C5: the tier bar covers the last 15 minutes). */
export const SERIES_LENGTH = 900;

/** The tier bar draws one column per 10 s: 90 columns, each with enough events to mean something. */
export const BUCKET_SECONDS = 10;
export const BUCKET_COUNT = SERIES_LENGTH / BUCKET_SECONDS + 1;

/** Events per tier since the previous snapshot; a counter that went backwards counts 0. */
export function tierDelta(previous: TierCounts, next: TierCounts): TierCounts {
  const delta = (tier: keyof TierCounts) => Math.max(0, next[tier] - previous[tier]);
  return { "1": delta("1"), "2": delta("2"), "3": delta("3"), "4": delta("4") };
}

export interface SeriesState {
  asOf: string | null;
  last: TierCounts | null;
  series: TierCounts[];
  /** Samples recorded so far, the absolute index just past `series`; it anchors the buckets. */
  total: number;
}

const EMPTY: SeriesState = { asOf: null, last: null, series: [], total: 0 };

/**
 * Sums per-second samples into `size`-second columns. Boundaries follow the absolute sample
 * index, so a new sample only changes the newest column and the rest shift once per bucket.
 */
export function bucketSeries(series: readonly TierCounts[], total: number, size: number = BUCKET_SECONDS): TierCounts[] {
  const first = total - series.length;
  const buckets: TierCounts[] = [];
  let key = Number.NaN;
  for (const [index, counts] of series.entries()) {
    const bucketKey = Math.floor((first + index) / size);
    if (bucketKey !== key) {
      buckets.push({ "1": 0, "2": 0, "3": 0, "4": 0 });
      key = bucketKey;
    }
    const bucket = buckets[buckets.length - 1] as TierCounts;
    for (const tier of ["1", "2", "3", "4"] as const) bucket[tier] += counts[tier];
  }
  return buckets;
}

/**
 * The series after one more snapshot (keyed by `as_of`, so a repeat changes nothing). A
 * snapshot carrying the server's own `tier_history` replaces what the browser collected;
 * one without it adds a single sample.
 */
export function nextSeries(current: SeriesState, overview: Overview): SeriesState {
  if (current.asOf === overview.as_of) return current;
  const last = overview.totals_by_tier;
  if (overview.tier_history) {
    const series = overview.tier_history.slice(-SERIES_LENGTH);
    return { asOf: overview.as_of, last, series, total: Math.max(current.total, series.length) };
  }
  if (!current.last) return { ...current, asOf: overview.as_of, last };
  const series = [...current.series, tierDelta(current.last, last)].slice(-SERIES_LENGTH);
  return { asOf: overview.as_of, last, series, total: current.total + 1 };
}

// One series per tenant scope, kept outside the page so leaving the Overview and coming back
// keeps the last 15 minutes. The shell's SSE handler records every tick, on any page.
const byScope = new Map<string, SeriesState>();
const listeners = new Set<() => void>();
const keyOf = (scope: string | null) => scope ?? "*";

export function recordOverview(scope: string | null, overview: Overview): void {
  const key = keyOf(scope);
  const current = byScope.get(key) ?? EMPTY;
  const next = nextSeries(current, overview);
  if (next === current) return;
  byScope.set(key, next);
  for (const listener of listeners) listener();
}

/** Tests only: forget every scope's series. */
export function resetTierSeries(): void {
  byScope.clear();
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function useSeriesState(overview: Overview | undefined, scope: string | null): SeriesState {
  useEffect(() => {
    if (overview) recordOverview(scope, overview);
  }, [overview, scope]);
  return useSyncExternalStore(subscribe, () => byScope.get(keyOf(scope)) ?? EMPTY);
}

/** One sample per second for `scope`, oldest first; records `overview` when it changes. */
export function useTierSeries(overview: Overview | undefined, scope: string | null = null): TierCounts[] {
  return useSeriesState(overview, scope).series;
}

/** The same series in `BUCKET_SECONDS` columns, for the tier bar. */
export function useTierBuckets(overview: Overview | undefined, scope: string | null = null): TierCounts[] {
  const state = useSeriesState(overview, scope);
  return useMemo(() => bucketSeries(state.series, state.total), [state]);
}
