import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router";
import {
  queryKeys,
  useContract,
  useDismissDrift,
  useDraft,
  useDriftItem,
  useLifecycle,
  useMe,
  useStartDraft,
  useStartReplay,
  useSubmitDraft,
} from "../../api/queries";
import type { DriftItem } from "../../api/types";
import { ageMs, formatAge } from "../../components/format";
import { DraftReview } from "../../components/review/DraftReview";
import { useNow } from "../../components/useNow";
import { useI18n } from "../../i18n/i18n";
import { ReplayProgress } from "./ReplayProgress";

/** In demo mode a draft still `drafting` after this long is re-requested from the cache (TC37). */
export const DEMO_FALLBACK_MS = 5_000;

const BUTTON = "rounded-control bg-thread px-3 py-1.5 text-paper disabled:opacity-50";
const QUIET = "rounded-control border border-rule px-3 py-1.5 text-ink disabled:opacity-50";
/** States a person can still act on; resolved and dismissed items are history. */
const UNRESOLVED = new Set(["open", "drafting", "draft_ready"]);

function Header({ item }: { item: DriftItem }) {
  const { t } = useI18n();
  const now = useNow(10_000);
  const first = ageMs(item.first_seen, now);
  const last = ageMs(item.last_seen, now);
  return (
    <header className="flex flex-col gap-2 border border-rule p-4">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <code className="font-mono text-meta text-ink-2">{item.source_id}</code>
        <span className="text-meta">{t(`drift.state.${item.state}`)}</span>
        <span className="font-medium tabular-nums">{t("drift.count", { n: item.count })}</span>
        {first !== null ? <span className="text-meta text-ink-2">{t("drift.firstSeen", { age: formatAge(first) })}</span> : null}
        {last !== null ? <span className="text-meta text-ink-2">{t("drift.lastSeen", { age: formatAge(last) })}</span> : null}
      </div>
      <h1 className="wrap-anywhere font-mono text-lead">{item.drain_template}</h1>
      {item.samples_masked.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-meta text-ink-2">{t("drift.samples")}</summary>
          <ul className="mt-1 flex flex-col gap-0.5">
            {item.samples_masked.map((sample, index) => (
              <li key={index} className="wrap-anywhere font-mono text-meta">
                {sample}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </header>
  );
}

/**
 * Beat 4 (C6): the drift item, its draft under full review, then submit → approve → promote →
 * replay, each step driven by the state of the contract version the draft became.
 */
export default function DriftDetailPage() {
  const { t } = useI18n();
  const { id = "" } = useParams();
  const client = useQueryClient();
  const me = useMe().data;
  const item = useDriftItem(id);
  const draftId = item.data?.draft_id ?? null;
  const draft = useDraft(draftId);
  const start = useStartDraft(id);
  const dismiss = useDismissDrift();
  const submit = useSubmitDraft(draftId ?? "");
  const contractId = draft.data?.contract_id ?? "";
  const contract = useContract(draft.data?.state === "submitted" || submit.isSuccess ? contractId : "");
  const lifecycle = useLifecycle(contractId);
  const replay = useStartReplay();
  const [jobId, setJobId] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const fellBack = useRef<string | null>(null);

  const drafting = draft.data?.state === "drafting";
  // The demo never waits on the model: after DEMO_FALLBACK_MS, ask again from the cache, once per draft.
  useEffect(() => {
    if (!me?.demo_mode || !drafting || draftId === null || fellBack.current === draftId) return;
    const timer = window.setTimeout(() => {
      fellBack.current = draftId;
      start.mutate("cache");
    }, DEMO_FALLBACK_MS);
    return () => window.clearTimeout(timer);
    // Not keyed on `start` (a new object each render): the timer depends only on the draft and the mode.
  }, [me?.demo_mode, drafting, draftId]);

  if (item.isPending) return <p className="p-6 text-ink-2">{t("common.loading")}</p>;
  if (item.isError) {
    return (
      <p role="alert" className="p-6">
        {t("common.error", { message: item.error.message })}
      </p>
    );
  }

  const version = contract.data?.versions.find((v) => v.draft_id === draftId) ?? null;
  const unresolved = item.data !== undefined && UNRESOLVED.has(item.data.state);
  // Noise (a one-off, a test line) can be dismissed until its draft becomes a contract version.
  const dismissable = unresolved && draft.data?.state !== "submitted" && !submit.isSuccess;
  const dismissButton = dismissable ? (
    <button type="button" disabled={dismiss.isPending} onClick={() => dismiss.mutate(id)} className={QUIET}>
      {t("drift.dismiss")}
    </button>
  ) : null;
  const act = async (step: () => Promise<unknown>) => {
    setRefusal(null);
    try {
      await step();
      void client.invalidateQueries({ queryKey: queryKeys.driftItem(id) });
    } catch (error) {
      setRefusal(error instanceof Error ? error.message : String(error));
    }
  };

  let actions = null;
  if (unresolved && (draftId === null || draft.data?.state === "failed")) {
    actions = (
      <div className="flex flex-wrap items-center gap-3">
        {draft.data?.state === "failed" ? <p role="alert">{t("drift.draftFailed", { detail: draft.data.detail })}</p> : null}
        <button type="button" disabled={start.isPending} onClick={() => start.mutate(undefined)} className={BUTTON}>
          {t("drift.draftIt")}
        </button>
        {dismissButton}
      </div>
    );
  } else if (drafting) {
    actions = (
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-ink-2">{t("drift.drafting")}</p>
        {dismissButton}
      </div>
    );
  } else if (dismissable) {
    actions = <div className="flex flex-wrap items-center gap-3">{dismissButton}</div>;
  } else if (version) {
    const canary = version.state === "canary";
    actions = (
      <div className="flex flex-col gap-3">
        <p className="text-meta text-ink-2">
          {t("drift.versionState", { id: version.contract_id, v: version.version, state: t(`contracts.state.${version.state}`) })}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          {canary && version.approved_by === null ? (
            <button
              type="button"
              disabled={lifecycle.approve.isPending}
              onClick={() => void act(() => lifecycle.approve.mutateAsync(version.version))}
              className={BUTTON}
            >
              {t("drift.approve")}
            </button>
          ) : null}
          {canary && version.approved_by !== null ? (
            <button
              type="button"
              disabled={lifecycle.promote.isPending}
              onClick={() => void act(() => lifecycle.promote.mutateAsync(version.version))}
              className={BUTTON}
            >
              {t("drift.promote")}
            </button>
          ) : null}
          {version.state === "active" && jobId === null && item.data ? (
            <button
              type="button"
              disabled={replay.isPending}
              onClick={() =>
                replay.mutate(
                  { contract_id: version.contract_id, template_sigs: [item.data.template_sig] },
                  { onSuccess: (job) => setJobId(job.job_id), onError: (error) => setRefusal(error.message) },
                )
              }
              className={BUTTON}
            >
              {t("drift.replay", { n: item.data.count })}
            </button>
          ) : null}
          {jobId && item.data ? <ReplayProgress jobId={jobId} sig={item.data.template_sig} /> : null}
          {refusal ? (
            <p role="alert" className="text-meta">
              {refusal}
            </p>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className="flex max-w-7xl flex-col gap-6 p-6">
      {item.data ? <Header item={item.data} /> : null}
      {draft.data && draft.data.state !== "drafting" && draft.data.templates.length > 0 ? (
        <DraftReview
          draft={draft.data}
          mode="full"
          onSubmit={unresolved && draft.data.state === "ready" && !version ? () => submit.mutate(undefined) : undefined}
          busy={submit.isPending}
        />
      ) : null}
      {[submit.error, start.error, dismiss.error].map((error) =>
        error ? (
          <p key={error.message} role="alert">
            {error.message}
          </p>
        ) : null,
      )}
      {actions}
    </div>
  );
}
