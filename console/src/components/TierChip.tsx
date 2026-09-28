import type { Tier } from "../api/types";
import { useI18n } from "../i18n/i18n";
import { TIER_BG } from "./tiers";

export function TierChip({ tier }: { tier: Tier }) {
  const { t } = useI18n();
  return (
    <span className="inline-flex items-center gap-1.5 text-meta text-ink">
      <span aria-hidden="true" className={`inline-block h-2.5 w-2.5 ${TIER_BG[tier]}`} />
      {t(`tier.${tier}`)}
    </span>
  );
}
