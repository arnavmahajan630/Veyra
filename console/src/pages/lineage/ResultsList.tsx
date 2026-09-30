import { Link } from "react-router";
import type { SearchHit, Tier } from "../../api/types";
import { TierChip } from "../../components/TierChip";
import { useI18n } from "../../i18n/i18n";

export interface ResultsListProps {
  hits: SearchHit[];
  onSelect?: (hit: SearchHit) => void;
}

export function ResultsList({ hits, onSelect }: ResultsListProps) {
  const { t } = useI18n();

  if (hits.length === 0) {
    return (
      <div className="rounded-md border border-edge bg-surface-1 p-8 text-center text-ink-3">
        {t("lineage.search.noResults") || "No events found"}
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-md border border-edge bg-surface-1">
      <div className="border-b border-edge bg-surface-2 px-4 py-2.5 text-meta font-medium text-ink-2">
        {t("lineage.search.resultsCount", { count: hits.length }) || `${hits.length} result(s)`}
      </div>
      <div className="divide-y divide-edge">
        {hits.map((hit) => {
          const tierNum = (hit.tier ?? 3) as Tier;
          return (
            <Link
              key={hit.event_uid}
              to={`/lineage/${hit.event_uid}`}
              onClick={() => onSelect?.(hit)}
              className="flex flex-col gap-2 p-4 transition-colors hover:bg-surface-2/60 focus:bg-surface-2/80 focus:outline-none"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-body font-semibold text-ink">
                    {hit.event_uid.slice(0, 18)}...
                  </span>
                  <span className="text-meta text-ink-3">({hit.source_id})</span>
                  <TierChip tier={tierNum} />
                  {hit.conformance && (
                    <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-micro text-ink-2">
                      {hit.conformance}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3 text-meta text-ink-3">
                  {hit.received_time && <span>{new Date(hit.received_time).toLocaleTimeString()}</span>}
                  {hit.revision && (
                    <span className="font-mono text-micro">rev {hit.revision}</span>
                  )}
                </div>
              </div>
              {hit.raw_preview && (
                <div className="truncate font-mono text-meta text-ink-2 bg-surface-2/40 px-2 py-1 rounded">
                  {hit.raw_preview}
                </div>
              )}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
