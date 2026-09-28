import { Link } from "react-router";
import type { DriftItem } from "../../api/types";
import { ageMs, formatAge } from "../../components/format";
import { useNow } from "../../components/useNow";
import { useI18n } from "../../i18n/i18n";

/**
 * One unknown message shape. Cards are the one card surface in the console (C6); each is a link,
 * so it's keyboard-reachable, and it has a fixed height, so new cards never shift the grid (AC5).
 */
export function DriftCard({ item, isNew }: { item: DriftItem; isNew: boolean }) {
  const { t } = useI18n();
  const now = useNow(10_000);
  const first = ageMs(item.first_seen, now);
  const last = ageMs(item.last_seen, now);
  return (
    <Link
      to={`/drift/${encodeURIComponent(item.drift_id)}`}
      data-new={isNew ? "true" : undefined}
      // New cards get the highlight wash for a few seconds; nothing moves (C5: no entrance animations).
      className={`flex h-44 flex-col gap-2 border p-4 hover:border-thread ${isNew ? "border-thread bg-highlight/40" : "border-rule bg-paper"}`}
    >
      <div className="flex items-baseline justify-between gap-3">
        <code className="truncate font-mono text-meta text-ink-2">{item.source_id}</code>
        <span className="shrink-0 text-meta">{t(`drift.state.${item.state}`)}</span>
      </div>
      <code className="line-clamp-3 break-all font-mono text-meta text-ink">{item.drain_template}</code>
      <div className="mt-auto flex flex-wrap gap-x-4 gap-y-0.5 text-meta text-ink-2">
        <span className="font-medium tabular-nums text-ink">{t("drift.count", { n: item.count })}</span>
        {first !== null ? <span>{t("drift.firstSeen", { age: formatAge(first) })}</span> : null}
        {last !== null ? <span>{t("drift.lastSeen", { age: formatAge(last) })}</span> : null}
      </div>
    </Link>
  );
}
