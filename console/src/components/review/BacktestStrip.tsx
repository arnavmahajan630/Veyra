import { Link } from "react-router";
import type { BacktestResult } from "../../api/types";
import { useI18n } from "../../i18n/i18n";

const tiers = (counts: Record<string, number>) => Object.keys(counts).map(Number).filter(Number.isFinite);

/** The backtest in one line: how many events move up a tier, regress, or stay (C6). */
export function BacktestStrip({ backtest, sig }: { backtest: BacktestResult; sig: string }) {
  const { t } = useI18n();
  const before = tiers(backtest.tier_before);
  const after = tiers(backtest.tier_after);
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-y border-rule py-2 text-body">
      {backtest.error ? (
        <p role="alert">{backtest.error}</p>
      ) : (
        <>
          {before.length > 0 && after.length > 0 ? (
            <span className="tabular-nums">
              {t("review.backtest", { before: Math.max(...before), after: Math.min(...after), n: backtest.upgraded })}
            </span>
          ) : null}
          <span className="tabular-nums">{t("review.regressions", { n: backtest.regressed })}</span>
          <span className="tabular-nums">{t("review.unchanged", { n: backtest.unchanged })}</span>
        </>
      )}
      <Link to={`/lineage?q=${encodeURIComponent(sig)}`} className="text-thread hover:underline">
        {t("review.viewEvents")}
      </Link>
    </div>
  );
}
