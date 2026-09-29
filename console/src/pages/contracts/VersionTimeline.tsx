import type { Transition } from "../../api/types";
import { ageMs, formatAge } from "../../components/format";
import { useNow } from "../../components/useNow";
import { useI18n } from "../../i18n/i18n";

/** The lifecycle history grouped by version, newest version first: action, actor, age. */
export function VersionTimeline({ history }: { history: readonly Transition[] }) {
  const { t } = useI18n();
  const now = useNow(10_000);
  const versions = [...new Set(history.map((h) => h.version))].sort((a, b) => b - a);
  return (
    <section aria-labelledby="contract-history">
      <h2 id="contract-history" className="mb-2 text-lead font-semibold">
        {t("contracts.history")}
      </h2>
      <ol className="flex flex-col gap-3">
        {versions.map((v) => (
          <li key={v}>
            <h3 className="font-medium">v{v}</h3>
            <ul className="mt-1 border-l border-rule pl-3">
              {history
                .filter((h) => h.version === v)
                .map((h, index) => {
                  const age = ageMs(h.at, now);
                  return (
                    // Separate cells rather than a middle-dot string (C5's default-avoidance review). The
                    // actor is who did it (the four-eyes record), so it wraps rather than truncates.
                    <li key={`${h.action}-${index}`} className="grid grid-cols-[6.5rem_minmax(0,1fr)_4.5rem] gap-2 py-0.5 text-meta">
                      <span className="font-medium text-ink">{t(`contracts.action.${h.action}`)}</span>
                      <span className="wrap-anywhere">{h.actor}</span>
                      <span className="text-right tabular-nums text-ink-2">{age === null ? "" : formatAge(age)}</span>
                    </li>
                  );
                })}
            </ul>
          </li>
        ))}
      </ol>
    </section>
  );
}
