import { useRef, useState } from "react";
import type { Draft, DraftMapping } from "../../api/types";
import { usePatchDraft } from "../../api/queries";
import { RawHighlighter, type HighlightSpan, type RawHighlighterHandle } from "../RawHighlighter";
import { ThreadOverlay } from "../ThreadOverlay";
import { useI18n } from "../../i18n/i18n";
import { BacktestStrip } from "./BacktestStrip";
import { MappingTable } from "./MappingTable";
import { buildRows, firstFailure, rowsToEdit } from "./mappings";

export interface DraftReviewProps {
  draft: Draft;
  mode: "compact" | "full";
  templateIndex?: number;
  onSubmit?: () => void;
  submitLabel?: string;
  busy?: boolean;
}

const titleCase = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export function DraftReview({ draft: given, mode, templateIndex = 0, onSubmit, submitLabel, busy }: DraftReviewProps) {
  const { t } = useI18n();
  const patch = usePatchDraft(given.draft_id);
  // An edit answers with the re-verified draft; show it at once (C6 AC4), whatever the parent holds.
  const draft = patch.data?.draft_id === given.draft_id ? patch.data : given;
  const template = draft.templates[templateIndex];
  const raw = useRef<RawHighlighterHandle>(null);
  const [activeSpan, setActiveSpan] = useState<string | null>(null);
  const [rowRect, setRowRect] = useState<DOMRect | null>(null);
  if (!template) return null;
  const rows = buildRows(template, draft.verification);
  const spans: HighlightSpan[] = rows
    .filter((r) => r.spanId)
    .map((r) => {
      const span = template.spans.find((s) => s.id === r.spanId)!;
      return { id: span.id, startChar: span.start, endChar: span.end, tone: span.id === activeSpan ? "active" : "pinned" };
    });
  const failure = firstFailure(draft.verification);
  const edit = (index: number, mapping: DraftMapping) => {
    const next = rowsToEdit(rows);
    next[index] = mapping;
    patch.mutate({ template_sig: template.template_sig, mappings: next });
  };
  const heading = `${titleCase(template.response.class)} / ${titleCase(template.response.activity)}`;
  const table = (
    <MappingTable
      rows={rows}
      template={template}
      editable={draft.state === "ready"}
      onHover={(spanId, rect) => { setActiveSpan(spanId); setRowRect(rect); }}
      onEdit={edit}
    />
  );
  const badge = (
    <>
      {/* A refused edit leaves the draft as it was; say why (a field mapped twice, a bad constant). */}
      {patch.error ? (
        <p role="alert" className="text-meta">
          {patch.error.message}
        </p>
      ) : null}
      <p className="text-meta text-ink-2">
        {t("review.draftedBy", { source: template.source, ms: Math.round(template.latency_ms) })}
      </p>
    </>
  );
  if (mode === "compact") {
    return (
      <section aria-label={t("review.title")} className="space-y-2">
        <p className="text-body">{heading}</p>
        {table}
        {badge}
      </section>
    );
  }
  return (
    <section aria-label={t("review.title")} className="grid gap-4 lg:grid-cols-2">
      <div className="space-y-3">
        <h3 className="text-lead">{t("review.raw")}</h3>
        <RawHighlighter ref={raw} text={template.sample_text} spans={spans} activeId={activeSpan ?? undefined}
                        onSpanHover={setActiveSpan} />
        <h3 className="text-lead">{t("review.pattern")}</h3>
        <pre className="font-mono text-meta whitespace-pre-wrap border border-rule p-2">{template.template.pattern}</pre>
      </div>
      <div className="space-y-3">
        <h3 className="text-lead">{t("review.mapping")}</h3>
        <p className="text-body">{heading}</p>
        {table}
        {badge}
      </div>
      <div className="lg:col-span-2">
        {draft.backtest ? <BacktestStrip backtest={draft.backtest} sig={template.template_sig} /> : null}
      </div>
      {onSubmit ? (
        <div className="flex items-center gap-3 lg:col-span-2">
          <button type="button" className="rounded-control bg-thread px-3 py-1 text-paper disabled:opacity-50"
                  disabled={failure !== null || busy} onClick={onSubmit}>
            {submitLabel ?? t("review.submit")}
          </button>
          {failure ? <p role="status" className="text-meta">{failure}</p> : null}
        </div>
      ) : null}
      <ThreadOverlay from={rowRect} to={activeSpan ? raw.current?.getSpanRect(activeSpan) ?? null : null} />
    </section>
  );
}
