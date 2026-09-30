// The verify chain: eight steps, revealed only once the server has answered.
//
// Honesty rule (B6): nothing animates green before the response arrives. The stagger is a
// presentation of a real result, not a progress bar. Three step states matter —
// passed, failed, and "not a failure": `pending_seal` (the window is not signed yet) and
// `not_implemented` (immudb is a prototype) both render grey.

import { AlertCircle, CheckCircle2, Circle, Clock, ShieldCheck, Undo2, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import { useDemoTamper, useDemoUntamper, useVerifyMutation } from "../../api/queries";
import type { VerifyReport, VerifyStep } from "../../api/types";
import { useHotkey } from "../../hotkeys/useHotkey";
import { useI18n } from "../../i18n/i18n";

export interface VerifyPanelProps {
  eventUid: string;
  demoMode?: boolean;
}

/** How long to wait before re-verifying once a pending seal is due. */
const PENDING_RETRY_MS = 5_000;
/** The demo's tamper → re-verify gap, long enough for the write to land. */
const TAMPER_REVERIFY_MS = 600;
const STAGGER_MS = 120;

/** A step that is neither a pass nor a failure: grey, and never breaks the thread. */
export function isNeutral(step: VerifyStep): boolean {
  return step.status === "not_implemented" || step.status === "pending_seal";
}

export function firstFailure(steps: VerifyStep[]): VerifyStep | undefined {
  return steps.find((step) => !step.ok && !isNeutral(step));
}

/**
 * The locating sentence under a failed chain, built from the failing steps' details —
 * "what broke, and what that tells you about when".
 */
export function failureSummary(steps: VerifyStep[]): string {
  const failures = steps.filter((step) => !step.ok && !isNeutral(step));
  if (failures.length === 0) return "";
  const intact = steps.filter((step) => step.ok).map((step) => step.id);
  const parts = failures.map((step) => step.detail || step.label);
  if (intact.includes("root_signature")) {
    parts.push("The signed root still verifies, so this change happened after the window was sealed.");
  }
  return parts.join(" ");
}

export function VerifyPanel({ eventUid, demoMode = false }: VerifyPanelProps) {
  const { t } = useI18n();
  const verifyMutation = useVerifyMutation();
  const tamperMutation = useDemoTamper();
  const untamperMutation = useDemoUntamper();

  const [report, setReport] = useState<VerifyReport | null>(null);
  const [revealedCount, setRevealedCount] = useState(0);
  const [selectedStep, setSelectedStep] = useState<VerifyStep | null>(null);
  // One retry per report, so a pending seal cannot become a polling loop.
  const retriedFor = useRef<string | null>(null);

  const runVerify = async () => {
    setRevealedCount(0);
    setSelectedStep(null);
    let result: VerifyReport;
    try {
      result = await verifyMutation.mutateAsync(eventUid);
    } catch {
      return; // surfaced through the mutation's error state below
    }
    setReport(result);
    // Reveal in order, after the answer is already in hand.
    result.steps.forEach((_step, index) => {
      setTimeout(() => setRevealedCount(index + 1), (index + 1) * STAGGER_MS);
    });

    const pending = result.steps.some((step) => step.status === "pending_seal");
    const token = `${eventUid}:${result.steps.length}`;
    if (pending && retriedFor.current !== token) {
      retriedFor.current = token;
      setTimeout(() => void runVerify(), PENDING_RETRY_MS);
    }
  };

  const tamperAndReverify = () => {
    tamperMutation.mutate(
      { mode: "insider_rewrite", event_uid: eventUid },
      { onSuccess: () => setTimeout(() => void runVerify(), TAMPER_REVERIFY_MS) },
    );
  };

  const untamperAndReverify = () => {
    untamperMutation.mutate(
      { event_uid: eventUid },
      { onSuccess: () => setTimeout(() => void runVerify(), TAMPER_REVERIFY_MS) },
    );
  };

  useHotkey({
    combo: "Shift+T",
    description: t("lineage.verify.hotkeyTamper") || "Tamper this event, then re-verify",
    when: () => demoMode && !!eventUid,
    handler: tamperAndReverify,
  });

  const steps = report?.steps ?? [];
  const failing = firstFailure(steps);
  const failingIndex = failing ? steps.indexOf(failing) : -1;
  const fullyRevealed = revealedCount >= steps.length && steps.length > 0;

  return (
    <div className="space-y-4 rounded-md border border-edge bg-surface-1 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-turmeric" />
          <h2 className="text-body font-semibold text-ink">
            {t("lineage.event.verify") || "Evidence verification"}
          </h2>
        </div>

        <div className="flex items-center gap-2">
          {demoMode && (
            <>
              <button
                type="button"
                onClick={tamperAndReverify}
                disabled={tamperMutation.isPending}
                className="rounded-md border border-edge px-3 py-1.5 text-meta font-medium text-ink-2 hover:bg-surface-2 disabled:opacity-50"
              >
                {t("lineage.verify.tamper") || "Tamper (Shift+T)"}
              </button>
              <button
                type="button"
                onClick={untamperAndReverify}
                disabled={untamperMutation.isPending}
                className="flex items-center gap-1.5 rounded-md border border-edge px-3 py-1.5 text-meta font-medium text-ink-2 hover:bg-surface-2 disabled:opacity-50"
              >
                <Undo2 className="h-3.5 w-3.5" />
                {t("lineage.verify.untamper") || "Untamper"}
              </button>
            </>
          )}
          <button
            type="button"
            onClick={() => void runVerify()}
            disabled={verifyMutation.isPending}
            className="rounded-md bg-turmeric px-3.5 py-1.5 text-body font-semibold text-surface-1 transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {verifyMutation.isPending
              ? t("common.verifying") || "Verifying…"
              : t("lineage.event.verifyBtn") || "Verify evidence"}
          </button>
        </div>
      </div>

      {verifyMutation.isError && (
        <p className="rounded-md border border-rose-500/30 bg-rose-500/5 p-3 text-meta text-rose-600">
          {t("lineage.verify.requestFailed") || "The verify request itself failed."}
        </p>
      )}

      {report && (
        <div className="space-y-4">
          <ol className="flex items-center gap-1.5 overflow-x-auto pb-2">
            {steps.map((step, index) => {
              const revealed = index < revealedCount;
              const neutral = isNeutral(step);
              const failed = !step.ok && !neutral;
              // The thread breaks at the first failure and stays broken after it.
              const linkBroken = failingIndex !== -1 && index >= failingIndex;

              return (
                <li key={step.id} className="flex shrink-0 items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => setSelectedStep(step)}
                    data-step={step.id}
                    data-state={!revealed ? "hidden" : failed ? "failed" : neutral ? "neutral" : "ok"}
                    className={`flex items-center gap-1.5 rounded border px-2.5 py-1 text-meta transition-all ${
                      !revealed
                        ? "border-edge bg-surface-2/40 text-ink-3"
                        : failed
                          ? "border-rose-500 bg-rose-500/10 font-semibold text-rose-500"
                          : neutral
                            ? "border-edge bg-surface-2 text-ink-3"
                            : "border-emerald-500/50 bg-emerald-500/10 font-medium text-emerald-600"
                    }`}
                  >
                    {!revealed ? (
                      <Circle className="h-3 w-3" />
                    ) : failed ? (
                      <XCircle className="h-3.5 w-3.5 text-rose-500" />
                    ) : neutral ? (
                      <Clock className="h-3 w-3 text-ink-3" />
                    ) : (
                      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                    )}
                    <span>{stepLabel(t, step)}</span>
                    {revealed && step.ms > 0 && (
                      <span className="font-mono text-micro text-ink-3">
                        {Math.round(step.ms)}ms
                      </span>
                    )}
                  </button>

                  {index < steps.length - 1 && (
                    <span
                      aria-hidden="true"
                      data-link={linkBroken ? "broken" : revealed ? "joined" : "idle"}
                      className={`h-0.5 w-3 ${
                        linkBroken
                          ? "border-t-2 border-dashed border-rose-500 bg-transparent"
                          : revealed
                            ? "bg-emerald-500"
                            : "bg-edge"
                      }`}
                    />
                  )}
                </li>
              );
            })}
          </ol>

          {failing && fullyRevealed && (
            <div className="flex items-start gap-2.5 rounded-md border border-rose-500/30 bg-rose-500/5 p-3 text-meta text-rose-600">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <p data-testid="verify-failure-summary">
                <span className="font-semibold">
                  {(t("lineage.verify.brokeAt") || "Evidence chain broke at {step}.").replace(
                    "{step}",
                    stepLabel(t, failing),
                  )}{" "}
                </span>
                <span>{failureSummary(steps)}</span>
              </p>
            </div>
          )}

          {selectedStep && (
            <div className="space-y-1 rounded border border-edge bg-surface-2/40 p-3 text-meta text-ink-2">
              <div className="font-semibold text-ink">{selectedStep.label}</div>
              {selectedStep.detail && <div>{selectedStep.detail}</div>}
              <div className="font-mono text-micro text-ink-3">
                {t("lineage.verify.latency") || "Latency"}: {Math.round(selectedStep.ms)} ms
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Step ids come from the API; their short chip labels are translated client-side. */
function stepLabel(t: (key: string) => string, step: VerifyStep): string {
  return t(`lineage.verify.steps.${step.id}`) || step.label || step.id;
}
