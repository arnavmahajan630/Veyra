import type { Tier, TierCounts } from "../api/types";

export const TIERS: readonly Tier[] = [1, 2, 3, 4];

/** Tier colours are for swatches and bars only (non-text contrast 3:1), never for text. */
export const TIER_BG: Record<Tier, string> = {
  1: "bg-tier1",
  2: "bg-tier2",
  3: "bg-tier3",
  4: "bg-tier4",
};

export const TIER_FILL: Record<Tier, string> = {
  1: "var(--color-tier1)",
  2: "var(--color-tier2)",
  3: "var(--color-tier3)",
  4: "var(--color-tier4)",
};

export function tierTotal(counts: TierCounts): number {
  return counts["1"] + counts["2"] + counts["3"] + counts["4"];
}

export function tierShares(counts: TierCounts): Record<Tier, number> {
  const total = tierTotal(counts);
  const share = (n: number) => (total === 0 ? 0 : n / total);
  return { 1: share(counts["1"]), 2: share(counts["2"]), 3: share(counts["3"]), 4: share(counts["4"]) };
}

export function percent(share: number): string {
  return `${Math.round(share * 100)}%`;
}
