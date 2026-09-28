import type { TierCounts } from "../api/types";
import { useI18n } from "../i18n/i18n";
import { TIER_BG, TIERS, percent, tierShares } from "./tiers";

export function MixBar({ counts }: { counts: TierCounts }) {
  const { t } = useI18n();
  const shares = tierShares(counts);
  const label = TIERS.map((tier) => `${t(`tier.${tier}`)} ${percent(shares[tier])}`).join(", ");
  return (
    <div role="img" aria-label={label} className="flex h-1.5 w-full overflow-hidden bg-rule">
      {TIERS.map((tier) => (
        <div key={tier} className={TIER_BG[tier]} style={{ flexGrow: shares[tier], flexBasis: 0 }} />
      ))}
    </div>
  );
}
