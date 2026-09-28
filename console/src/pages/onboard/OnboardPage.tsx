import { useState } from "react";
import { useSearchParams } from "react-router";
import { useContractVersion, useDraft } from "../../api/queries";
import { useI18n } from "../../i18n/i18n";
import { AnalysisStep } from "./AnalysisStep";
import { ApprovalStep } from "./ApprovalStep";
import { DraftStep } from "./DraftStep";
import { KeyStep } from "./KeyStep";
import { SamplesStep } from "./SamplesStep";
import { SourceStep } from "./SourceStep";

type Progress = Partial<Record<"source" | "draft" | "contract" | "version", string>>;

/**
 * Beat 2 (C6): source → samples → analysis → draft → approval → key, each section unlocking the
 * next. Progress lives in the URL, so a reload or a demo user switch lands on the same step.
 */
export default function OnboardPage() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const [analysis, setAnalysis] = useState<{ samples: string; run: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const source = params.get("source");
  const draftId = params.get("draft");
  const contract = params.get("contract");
  const version = Number(params.get("version")) || null;
  const draft = useDraft(draftId);
  const submitted = useContractVersion(contract ?? "", contract ? version : null);
  const sourceId = source ?? draft.data?.source_id ?? null;

  const advance = (next: Progress) =>
    setParams((current) => {
      const merged = new URLSearchParams(current);
      for (const [key, value] of Object.entries(next)) merged.set(key, value);
      return merged;
    });

  return (
    <div className="flex max-w-6xl flex-col gap-8 p-6">
      <h1 className="text-title font-semibold">{t("onboard.title")}</h1>
      <SourceStep sourceId={source} onCreated={(id) => advance({ source: id })} />
      {source ? (
        <SamplesStep busy={busy} onAnalyze={(samples) => setAnalysis((current) => ({ samples, run: (current?.run ?? 0) + 1 }))} />
      ) : null}
      {source && analysis ? (
        <AnalysisStep
          sourceId={source}
          samples={analysis.samples}
          run={analysis.run}
          onBusy={setBusy}
          onDraft={(id) => advance({ draft: id })}
          onLibrary={(v) => advance({ contract: v.contract_id, version: String(v.version) })}
        />
      ) : null}
      {draftId ? (
        <DraftStep draftId={draftId} onSubmitted={(v) => advance({ contract: v.contract_id, version: String(v.version) })} />
      ) : null}
      {contract && version ? <ApprovalStep contractId={contract} version={version} /> : null}
      {submitted.data?.state === "active" && sourceId ? <KeyStep sourceId={sourceId} /> : null}
    </div>
  );
}
