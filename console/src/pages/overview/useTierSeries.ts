import { useEffect, useState } from "react";
import type { Overview, TierCounts } from "../../api/types";

/** 15 minutes of 1 s ticks (C5: the tier bar covers the last 15 minutes). */
export const SERIES_LENGTH = 900;

/** Events per tier since the previous snapshot; a counter that went backwards counts 0. */
export function tierDelta(previous: TierCounts, next: TierCounts): TierCounts {
  const delta = (tier: keyof TierCounts) => Math.max(0, next[tier] - previous[tier]);
  return { "1": delta("1"), "2": delta("2"), "3": delta("3"), "4": delta("4") };
}

interface SeriesState {
  asOf: string | null;
  last: TierCounts | null;
  series: TierCounts[];
}

/** One sample per new overview snapshot (keyed by `as_of`), oldest first. */
export function useTierSeries(overview: Overview | undefined): TierCounts[] {
  const [state, setState] = useState<SeriesState>({ asOf: null, last: null, series: [] });

  useEffect(() => {
    if (!overview) return;
    setState((current) => {
      if (current.asOf === overview.as_of) return current;
      const series = current.last
        ? [...current.series, tierDelta(current.last, overview.totals_by_tier)].slice(-SERIES_LENGTH)
        : current.series;
      return { asOf: overview.as_of, last: overview.totals_by_tier, series };
    });
  }, [overview]);

  return state.series;
}
