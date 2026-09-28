import type { TierCounts } from "../api/types";
import { useI18n } from "../i18n/i18n";
import { TIER_FILL, TIERS, tierShares } from "./tiers";

const HEIGHT = 100;

export interface TierBarProps {
  series: readonly TierCounts[];
  /** Fixed number of slots (a time axis): the newest sample sits at the right edge and the
   *  bar fills leftwards. Without it the samples stretch across the full width. */
  capacity?: number;
}

/** Stacked tier mix per sample, oldest on the left; tier 1 at the bottom. */
export function TierBar({ series, capacity }: TierBarProps) {
  const { t } = useI18n();
  const width = Math.max(capacity ?? series.length, series.length, 1);
  const offset = width - series.length;
  return (
    <svg
      role="img"
      aria-label={t("overview.tierMix")}
      viewBox={`0 0 ${width} ${HEIGHT}`}
      preserveAspectRatio="none"
      // Stretched columns anti-alias into pale seams; crisp edges keep the bar solid.
      shapeRendering="crispEdges"
      className="h-24 w-full bg-rule/30"
    >
      {series.map((counts, index) => {
        const shares = tierShares(counts);
        let y = HEIGHT;
        return (
          <g key={index} data-sample="">
            {TIERS.map((tier) => {
              const height = shares[tier] * HEIGHT;
              y -= height;
              return (
                <rect key={tier} x={offset + index} y={y} width={1} height={height} fill={TIER_FILL[tier]} />
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}
