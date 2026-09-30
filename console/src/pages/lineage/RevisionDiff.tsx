// What changed between two revisions of the same event.
//
// The comparison is by value, not by offset: a field that stops being located but keeps
// its value is not a change (the convention A5's shadow diff uses). Added and changed
// rows carry the two accent colours the design system reserves for a diff.

import type { EventRevision } from "../../api/types";
import { useI18n } from "../../i18n/i18n";
import { buildFields } from "./fields";

export interface RevisionDiffProps {
  before: EventRevision;
  after: EventRevision;
  rawText?: string | null;
}

type Change = { path: string; kind: "added" | "changed" | "removed"; from?: string; to?: string };

export function diffRevisions(
  before: EventRevision,
  after: EventRevision,
  rawText?: string | null,
): Change[] {
  const valuesOf = (revision: EventRevision) =>
    new Map(buildFields(revision, rawText).map((f) => [f.path, f.value]));
  const older = valuesOf(before);
  const newer = valuesOf(after);
  const changes: Change[] = [];

  for (const [path, value] of newer) {
    if (!older.has(path)) changes.push({ path, kind: "added", to: value });
    else if (older.get(path) !== value)
      changes.push({ path, kind: "changed", from: older.get(path), to: value });
  }
  for (const [path, value] of older) {
    if (!newer.has(path)) changes.push({ path, kind: "removed", from: value });
  }
  return changes.sort((a, b) => a.path.localeCompare(b.path));
}

export function RevisionDiff({ before, after, rawText }: RevisionDiffProps) {
  const { t } = useI18n();
  const changes = diffRevisions(before, after, rawText);

  return (
    <div className="rounded-md border border-edge bg-surface-1 p-3">
      <h2 className="mb-2 text-meta font-semibold text-ink">
        {t("lineage.diff.title") || "Changes"} · rev {before.revision} → rev {after.revision}
      </h2>
      {changes.length === 0 ? (
        <p className="text-meta text-ink-3">
          {t("lineage.diff.none") || "No mapped field changed between these revisions."}
        </p>
      ) : (
        <ul className="space-y-1 font-mono text-meta">
          {changes.map((change) => (
            <li key={`${change.kind}:${change.path}`} className="flex flex-wrap items-baseline gap-2">
              <span
                className={
                  change.kind === "removed"
                    ? "text-violet-500"
                    : change.kind === "added"
                      ? "text-emerald-600"
                      : "text-turmeric"
                }
              >
                {change.kind === "removed" ? "−" : change.kind === "added" ? "+" : "~"}
              </span>
              <span className="text-ink-2">{change.path}</span>
              {change.from !== undefined && (
                <span className="text-violet-500 line-through">{change.from}</span>
              )}
              {change.to !== undefined && <span className="text-emerald-600">{change.to}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
