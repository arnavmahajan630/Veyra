import { Link } from "react-router";
import { useDrift } from "../../api/queries";
import { useI18n } from "../../i18n/i18n";

/** The source's open unknown message shapes with their counts (C5's drawer bullet, built in C6). */
export function SourceDriftList({ sourceId }: { sourceId: string }) {
  const { t } = useI18n();
  const items = useDrift("open", sourceId).data ?? [];
  return (
    <section aria-labelledby="source-drift" className="mt-6">
      <h3 id="source-drift" className="mb-2 font-semibold">
        {t("sources.drawer.driftTitle")}
      </h3>
      {items.length === 0 ? (
        <p className="text-ink-2">{t("sources.drawer.noDrift")}</p>
      ) : (
        <ul className="divide-y divide-rule border-y border-rule">
          {items.map((item) => (
            <li key={item.drift_id} className="flex items-baseline gap-3 py-1.5">
              <Link
                to={`/drift/${encodeURIComponent(item.drift_id)}`}
                className="min-w-0 flex-1 truncate font-mono text-meta text-thread hover:underline"
              >
                {item.drain_template}
              </Link>
              <span className="tabular-nums">{item.count}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
