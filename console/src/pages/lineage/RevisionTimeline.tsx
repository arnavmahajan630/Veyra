import { ArrowRight, GitCommit } from "lucide-react";
import type { EventRevision, Tier } from "../../api/types";
import { TierChip } from "../../components/TierChip";
import { useI18n } from "../../i18n/i18n";

export interface RevisionTimelineProps {
  revisions: EventRevision[];
  activeRevision: number;
  onSelectRevision: (rev: number) => void;
  showDiff: boolean;
  onToggleDiff: (show: boolean) => void;
}

export function RevisionTimeline({
  revisions,
  activeRevision,
  onSelectRevision,
  showDiff,
  onToggleDiff,
}: RevisionTimelineProps) {
  const { t } = useI18n();

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-edge bg-surface-1 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-meta font-medium text-ink-2">
          <GitCommit className="h-4 w-4 text-ink-3" />
          {t("lineage.event.revisions") || "Revisions"}:
        </span>

        <div className="flex flex-wrap items-center gap-1.5">
          {revisions.map((rev, index) => {
            const isSelected = rev.revision === activeRevision;
            return (
              <div key={rev.revision} className="flex items-center gap-1.5">
                {index > 0 && <ArrowRight className="h-3.5 w-3.5 text-ink-3" />}
                <button
                  type="button"
                  onClick={() => onSelectRevision(rev.revision)}
                  className={`flex items-center gap-2 rounded-md border px-2.5 py-1 text-meta transition-all ${
                    isSelected
                      ? "border-turmeric bg-surface-2 font-semibold text-ink shadow-sm"
                      : "border-edge bg-surface-1 text-ink-2 hover:bg-surface-2/60"
                  }`}
                >
                  <span className="font-mono text-micro font-bold">rev {rev.revision}</span>
                  <TierChip tier={(rev.tier ?? 3) as Tier} />
                  {rev.contract_ref && (
                    <span className="font-mono text-micro text-ink-3">
                      {rev.contract_ref}
                    </span>
                  )}
                  {rev.replay_job_id && (
                    <span className="rounded bg-surface-3 px-1 text-micro text-ink-3">
                      job {rev.replay_job_id.slice(0, 6)}
                    </span>
                  )}
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {revisions.length > 1 && (
        <button
          type="button"
          onClick={() => onToggleDiff(!showDiff)}
          className={`rounded border px-2 py-1 text-meta transition-colors ${
            showDiff
              ? "border-turmeric bg-highlight/30 text-ink font-medium"
              : "border-edge bg-surface-2 text-ink-2 hover:bg-surface-3"
          }`}
        >
          {showDiff ? t("lineage.event.hideDiff") || "Hide diff" : t("lineage.event.showDiff") || "Show diff"}
        </button>
      )}
    </div>
  );
}
