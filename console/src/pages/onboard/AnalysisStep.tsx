import { useEffect, useState } from "react";
import { CONTROL } from "../../api/client";
import { useUseLibrary } from "../../api/queries";
import { postStream } from "../../api/stream";
import type { AnalyzeEvent, LibraryMatch, VersionDetail } from "../../api/types";
import { useI18n } from "../../i18n/i18n";
import { describeLayers, type EnvelopeLayer } from "./layers";

interface Analysis {
  layers: string | null;
  shapes: { template_sig: string; drain_template: string; count: number }[];
  library: { matches: LibraryMatch[]; matched: string | null } | null;
  drafted: { template_sig: string; source: string; pattern: string }[];
  error: string | null;
  done: boolean;
}

const EMPTY: Analysis = { layers: null, shapes: [], library: null, drafted: [], error: null, done: false };

function reduce(state: Analysis, event: AnalyzeEvent): Analysis {
  switch (event.event) {
    case "classification":
      return { ...state, layers: describeLayers(event.data.layers as EnvelopeLayer[]) };
    case "templates":
      return { ...state, shapes: event.data };
    case "library":
      return { ...state, library: event.data };
    case "draft":
      return { ...state, drafted: [...state.drafted, event.data] };
    case "done":
      return { ...state, done: true };
    case "error":
      return { ...state, error: event.data.message, done: true };
  }
}

export interface AnalysisStepProps {
  sourceId: string;
  samples: string;
  /** Changes on every Analyze click, so the same samples can be analyzed again. */
  run: number;
  onDraft: (draftId: string) => void;
  onLibrary: (version: VersionDetail) => void;
  onBusy: (busy: boolean) => void;
}

/** Step 3: stream the analysis (TC42) and show layers, shapes, the library match and drafts. */
export function AnalysisStep({ sourceId, samples, run, onDraft, onLibrary, onBusy }: AnalysisStepProps) {
  const { t } = useI18n();
  const useLibrary = useUseLibrary();
  const [analysis, setAnalysis] = useState<Analysis>(EMPTY);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    setAnalysis(EMPTY);
    onBusy(true);
    postStream(
      `${CONTROL}/onboarding/analyze`,
      { source_id: sourceId, samples: [samples] },
      (frame) => {
        const event = frame as AnalyzeEvent;
        setAnalysis((current) => reduce(current, event));
        if (event.event === "done" && event.data.draft_id) onDraft(event.data.draft_id);
      },
      abort.signal,
    )
      .catch((error: unknown) => {
        if (!abort.signal.aborted) {
          setAnalysis((current) => ({ ...current, error: error instanceof Error ? error.message : String(error), done: true }));
        }
      })
      .finally(() => {
        if (!abort.signal.aborted) onBusy(false);
      });
    return () => abort.abort();
    // Only a new run restarts the analysis; the page's callbacks change on every render.
  }, [sourceId, samples, run, attempt]);

  const best = analysis.library?.matches[0];
  return (
    <section aria-labelledby="onboard-analysis" aria-busy={!analysis.done}>
      <h2 id="onboard-analysis" className="text-lead font-semibold">
        {t("onboard.analysis")}
      </h2>
      {!analysis.done && analysis.layers === null ? <p className="mt-1 text-ink-2">{t("onboard.analyzing")}</p> : null}
      {analysis.layers ? <p className="mt-1">{t("onboard.layers", { layers: analysis.layers })}</p> : null}
      {analysis.shapes.length > 0 ? (
        <>
          <h3 className="mt-3 font-medium">{t("onboard.shapes")}</h3>
          <ul className="mt-1 divide-y divide-rule border-y border-rule">
            {analysis.shapes.map((shape) => (
              <li key={shape.template_sig} className="flex items-baseline gap-4 py-1.5">
                <span className="w-20 shrink-0 tabular-nums text-ink-2">{t("onboard.shapeCount", { n: shape.count })}</span>
                <code className="min-w-0 wrap-anywhere font-mono text-meta">{shape.drain_template}</code>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {analysis.library ? (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <p>
            {analysis.library.matched && best
              ? t("onboard.libraryMatch", { id: analysis.library.matched, pct: Math.round(best.tier1_pct * 100) })
              : t("onboard.noLibrary")}
          </p>
          {analysis.library.matched ? (
            <button
              type="button"
              disabled={useLibrary.isPending}
              onClick={() =>
                useLibrary.mutate(
                  { source_id: sourceId, pack: analysis.library?.matched ?? "" },
                  { onSuccess: (version) => onLibrary(version) },
                )
              }
              className="rounded-control bg-thread px-3 py-1.5 text-paper disabled:opacity-50"
            >
              {t("onboard.useLibrary", { id: analysis.library.matched })}
            </button>
          ) : null}
        </div>
      ) : null}
      {analysis.drafted.length > 0 ? (
        <ul className="mt-3 flex flex-col gap-1">
          {analysis.drafted.map((draft) => (
            <li key={draft.template_sig} className="text-meta">
              {t("onboard.drafted", { pattern: draft.pattern, source: draft.source })}
            </li>
          ))}
        </ul>
      ) : null}
      {analysis.error || useLibrary.error ? (
        <div className="mt-3 flex items-center gap-3">
          <p role="alert">{analysis.error ?? useLibrary.error?.message}</p>
          {analysis.error ? (
            <button type="button" onClick={() => setAttempt((n) => n + 1)} className="text-thread hover:underline">
              {t("onboard.retry")}
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
