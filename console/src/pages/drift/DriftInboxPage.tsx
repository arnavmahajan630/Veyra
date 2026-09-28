import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { useDrift } from "../../api/queries";
import type { DriftItem, DriftState } from "../../api/types";
import { useI18n } from "../../i18n/i18n";
import { DriftCard } from "./DriftCard";

const CHIPS = ["open", "draft_ready", "resolved", "dismissed"] as const;
type Chip = (typeof CHIPS)[number];
/** "Open" is every unresolved item: control-api filters on one exact state, so the page groups these. */
const UNRESOLVED = new Set<DriftState>(["open", "drafting", "draft_ready"]);
/** How long a newly arrived card stays marked. */
export const NEW_MS = 3_000;

/** Ids that appear after the first load of a filter, each for `NEW_MS`. */
function useFreshIds(items: readonly DriftItem[] | undefined, filterKey: string): ReadonlySet<string> {
  const known = useRef<{ key: string; ids: Set<string> } | null>(null);
  const [fresh, setFresh] = useState<ReadonlySet<string>>(new Set());
  const timers = useRef<number[]>([]);
  useEffect(() => () => timers.current.forEach((timer) => window.clearTimeout(timer)), []);
  useEffect(() => {
    if (!items) return;
    if (known.current?.key !== filterKey) {
      known.current = { key: filterKey, ids: new Set(items.map((i) => i.drift_id)) };
      return;
    }
    const seen = known.current.ids;
    const added = items.map((i) => i.drift_id).filter((id) => !seen.has(id));
    if (added.length === 0) return;
    for (const id of added) seen.add(id);
    setFresh((current) => new Set([...current, ...added]));
    timers.current.push(
      window.setTimeout(
        () => setFresh((current) => new Set([...current].filter((id) => !added.includes(id)))),
        NEW_MS,
      ),
    );
  }, [items, filterKey]);
  return fresh;
}

/** The drift inbox (C6, Beat 4): state chips, an optional source filter, and a card per shape. */
export default function DriftInboxPage() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const chip: Chip = CHIPS.find((c) => c === params.get("state")) ?? "open";
  const source = params.get("source");
  const drift = useDrift(chip === "open" ? null : chip, source);
  const items = (drift.data ?? []).filter((i) => chip !== "open" || UNRESOLVED.has(i.state));
  const fresh = useFreshIds(drift.data, `${chip}|${source ?? ""}`);

  const update = (key: "state" | "source", value: string | null) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value === null) next.delete(key);
      else next.set(key, value);
      return next;
    });

  return (
    <div className="flex flex-col gap-4 p-6">
      <h1 className="text-title font-semibold">{t("drift.title")}</h1>
      <div className="flex flex-wrap items-center gap-2">
        {CHIPS.map((c) => (
          <button
            key={c}
            type="button"
            aria-pressed={chip === c}
            onClick={() => update("state", c === "open" ? null : c)}
            className={`rounded-control border px-3 py-1 ${chip === c ? "border-thread bg-thread text-paper" : "border-rule text-ink"}`}
          >
            {t(`drift.chip.${c}`)}
          </button>
        ))}
        {source ? (
          <span className="ml-2 flex items-center gap-2 text-meta">
            <span>{t("drift.onlySource", { source })}</span>
            <button type="button" onClick={() => update("source", null)} className="text-thread hover:underline">
              {t("drift.allSources")}
            </button>
          </span>
        ) : null}
      </div>
      {drift.isPending ? (
        <p className="text-ink-2">{t("common.loading")}</p>
      ) : drift.isError ? (
        <p role="alert">{t("common.error", { message: drift.error.message })}</p>
      ) : items.length === 0 ? (
        <p>{t("drift.empty")}</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {items.map((item) => (
            <DriftCard key={item.drift_id} item={item} isNew={fresh.has(item.drift_id)} />
          ))}
        </div>
      )}
    </div>
  );
}
