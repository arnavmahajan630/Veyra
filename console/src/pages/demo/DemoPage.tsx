// The hidden demo panel (B7). Stage buttons with their live `expect` results, the reset with
// its step-by-step progress, the preflight table, the tamper lab, and the beat timer.
//
// Two things here are deliberate: a stage is "done" only when the engine says its
// expectations held (not merely because the POST returned), and every hotkey goes through
// C5's registry with a demo-mode guard, so nothing fires on a non-demo machine.

import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  HelpCircle,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Undo2,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  useDemoReset,
  useDemoScenario,
  useDemoStage,
  useDemoTamper,
  useDemoUntamper,
  useLineageSearch,
  usePreflight,
  useReportHotkeys,
  useResetStatus,
  useTamperActive,
  useTriggerStage,
  useMe,
} from "../../api/queries";
import type { PreflightCheck } from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useHotkey } from "../../hotkeys/useHotkey";
import { useI18n } from "../../i18n/i18n";
import { useTenantScope } from "../../shell/tenant";

/** The beats of 04_DEMO_SCRIPT, so the timer can say where a rehearsal is. */
const BEATS: { stage: number; title: string; startsAt: number; endsAt: number }[] = [
  { stage: 1, title: "Hook", startsAt: 0, endsAt: 15 },
  { stage: 2, title: "Onboard an org source", startsAt: 15, endsAt: 40 },
  { stage: 3, title: "Log storm", startsAt: 40, endsAt: 85 },
  { stage: 4, title: "Drift → draft → approve → replay", startsAt: 85, endsAt: 130 },
  { stage: 5, title: "Traceability and tamper-evidence", startsAt: 130, endsAt: 170 },
  { stage: 6, title: "Close", startsAt: 170, endsAt: 180 },
];

const DEMO_HOTKEYS = ["Shift+1", "Shift+2", "Shift+3", "Shift+4", "Shift+5", "Shift+6", "Shift+T", "Shift+R"];

/**
 * A hotkey pressed twice in quick succession is one intent, not two (B7 AC3). The engine is
 * idempotent while a stage runs, but a stage that finishes fast would otherwise re-run on
 * the second tap of a fumbled keypress.
 */
const STAGE_COOLDOWN_MS = 1_500;

function mmss(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function DemoPage() {
  const { t } = useI18n();
  const { data: me } = useMe();
  const demoMode = !!me?.demo_mode;

  const { data: scenario } = useDemoScenario();
  const { data: stageStatus } = useDemoStage(true);
  const { data: preflight, refetch: refetchPreflight, isFetching: preflightFetching } = usePreflight();
  const { data: tamperState } = useTamperActive();

  const triggerStage = useTriggerStage();
  const startReset = useDemoReset();
  const tamper = useDemoTamper();
  const untamper = useDemoUntamper();
  const reportHotkeys = useReportHotkeys();

  const [resetOpen, setResetOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  const { data: resetStatus } = useResetStatus(resetting);

  // Tell the engine our hotkeys are live, which is one of preflight's checks.
  const pinged = useRef(false);
  useEffect(() => {
    if (!demoMode || pinged.current) return;
    pinged.current = true;
    reportHotkeys.mutate(DEMO_HOTKEYS);
    // reportHotkeys is a stable mutation object; this must fire once per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demoMode]);

  useEffect(() => {
    if (resetting && resetStatus && !resetStatus.running) setResetting(false);
  }, [resetting, resetStatus]);

  const stages = useMemo(() => {
    const entries = Object.entries(scenario?.stages ?? {});
    return entries
      .map(([number, info]) => ({ number: Number(number), ...info }))
      .sort((a, b) => a.number - b.number);
  }, [scenario]);

  const lastRun = useRef<Record<number, number>>({});
  const runStage = (number: number) => {
    if (triggerStage.isPending) return;
    if (stageStatus?.stage === number && stageStatus.state === "running") return;
    const now = Date.now();
    if (now - (lastRun.current[number] ?? 0) < STAGE_COOLDOWN_MS) return;
    lastRun.current[number] = now;
    triggerStage.mutate(number);
  };

  for (const number of [1, 2, 3, 4, 5, 6]) {
    // One registration per stage; `when` keeps them inert outside demo mode.
    useHotkey({
      combo: `Shift+${number}`,
      description: t("demo.hotkey.stage", { n: String(number) }) || `Run demo stage ${number}`,
      when: () => demoMode,
      handler: () => runStage(number),
    });
  }

  useHotkey({
    combo: "Shift+R",
    description: t("demo.hotkey.reset") || "Reset the demo (asks first)",
    when: () => demoMode,
    handler: () => setResetOpen(true),
  });

  return (
    <div className="space-y-6 p-6">
      <header>
        <h1 className="text-title font-semibold text-ink">{t("demo.title")}</h1>
        <p className="mt-1 text-body text-ink-3">
          {t("demo.description")}
          {scenario ? ` · ${scenario.scenario}` : ""}
        </p>
      </header>

      <StagePanel
        stages={stages}
        status={stageStatus}
        onRun={runStage}
        pending={triggerStage.isPending}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ResetPanel
          onAsk={() => setResetOpen(true)}
          running={resetting}
          status={resetStatus}
        />
        <BeatTimer />
      </div>

      <PreflightPanel
        checks={preflight}
        fetching={preflightFetching}
        onRefresh={() => void refetchPreflight()}
      />

      <TamperPanel
        modes={tamperState?.modes ?? []}
        active={tamperState?.active ?? []}
        onTamper={(mode, eventUid) => tamper.mutate({ mode, event_uid: eventUid })}
        onUntamper={(eventUid) => untamper.mutate({ event_uid: eventUid })}
        busy={tamper.isPending || untamper.isPending}
        result={tamper.data}
      />

      <ConfirmDialog
        open={resetOpen}
        title={t("demo.reset.confirmTitle") || "Reset the whole demo?"}
        body={
          t("demo.reset.confirmBody") ||
          "Kafka topics, the lineage index, the evidence vault and the control database are wiped and reseeded. The signing keys are kept."
        }
        confirmLabel={t("demo.reset.confirm") || "Reset now"}
        busy={startReset.isPending}
        onConfirm={() => {
          startReset.mutate(undefined, { onSuccess: () => setResetting(true) });
          setResetOpen(false);
        }}
        onCancel={() => setResetOpen(false)}
      />
    </div>
  );
}

// ---------------------------------------------------------------- stages
function StagePanel({
  stages,
  status,
  onRun,
  pending,
}: {
  stages: { number: number; title: string; expects?: string[] }[];
  status?: { stage: number; state: string; results?: Record<string, boolean>; error?: string | null };
  onRun: (n: number) => void;
  pending: boolean;
}) {
  const { t } = useI18n();
  return (
    <section className="rounded-md border border-edge bg-surface-1 p-4">
      <h2 className="mb-3 text-meta font-semibold text-ink">{t("demo.stages.title")}</h2>
      {stages.length === 0 ? (
        <p className="text-meta text-ink-3">
          {t("demo.stages.none") || "The demo engine is not reachable, so no scenario is loaded."}
        </p>
      ) : (
        <ul className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
          {stages.map((stage) => {
            const current = status?.stage === stage.number;
            const state = current ? status?.state ?? "idle" : "idle";
            return (
              <li key={stage.number}>
                <button
                  type="button"
                  onClick={() => onRun(stage.number)}
                  disabled={pending || state === "running"}
                  data-stage={stage.number}
                  data-state={state}
                  className={`w-full rounded-md border p-3 text-left transition-colors disabled:opacity-60 ${
                    state === "running"
                      ? "border-turmeric bg-highlight/40"
                      : state === "done"
                        ? "border-emerald-500/40 bg-emerald-500/5"
                        : state === "failed"
                          ? "border-rose-500/40 bg-rose-500/5"
                          : "border-edge hover:bg-surface-2"
                  }`}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="text-body font-medium text-ink">
                      {stage.number}. {stage.title}
                    </span>
                    <kbd className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-micro text-ink-3">
                      ⇧{stage.number}
                    </kbd>
                  </span>
                  <span className="mt-1 block text-micro text-ink-3">{stateLabel(t, state)}</span>
                </button>

                {current && (status?.results || stage.expects?.length) ? (
                  <ul className="mt-1 space-y-0.5 pl-1">
                    {(stage.expects ?? []).map((label) => {
                      const ok = status?.results?.[label];
                      return (
                        <li
                          key={label}
                          data-expect={label}
                          data-ok={ok === undefined ? "pending" : String(ok)}
                          className="flex items-start gap-1.5 text-micro"
                        >
                          {ok === undefined ? (
                            <Clock className="mt-0.5 h-3 w-3 shrink-0 text-ink-3" />
                          ) : ok ? (
                            <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" />
                          ) : (
                            <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-rose-500" />
                          )}
                          <span className="text-ink-2">{label}</span>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}

                {current && status?.error ? (
                  <p className="mt-1 pl-1 text-micro text-rose-600">{status.error}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function stateLabel(t: (key: string) => string, state: string): string {
  if (state === "running") return t("demo.stage.running") || "running…";
  if (state === "done") return t("demo.stage.done") || "done, expectations held";
  if (state === "failed") return t("demo.stage.failed") || "failed";
  return t("demo.stage.idle") || "idle";
}

// ---------------------------------------------------------------- reset
function ResetPanel({
  onAsk,
  running,
  status,
}: {
  onAsk: () => void;
  running: boolean;
  status?: { running: boolean; ok: boolean | null; seconds: number; over_budget: boolean; steps: { name: string; ok: boolean; ms: number; detail?: string }[] };
}) {
  const { t } = useI18n();
  return (
    <section className="rounded-md border border-edge bg-surface-1 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-meta font-semibold text-ink">{t("demo.reset.title")}</h2>
        <button
          type="button"
          onClick={onAsk}
          disabled={running}
          className="flex items-center gap-1.5 rounded-md border border-edge px-3 py-1.5 text-meta font-medium text-ink-2 hover:bg-surface-2 disabled:opacity-50"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          {running ? t("demo.reset.running") || "Resetting…" : t("demo.reset.button") || "Reset (⇧R)"}
        </button>
      </div>

      {status && status.steps.length > 0 ? (
        <>
          <ol className="space-y-1">
            {status.steps.map((step) => (
              <li key={step.name} data-step={step.name} className="flex items-start gap-2 text-micro">
                {step.ok ? (
                  <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" />
                ) : (
                  <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-rose-500" />
                )}
                <span className="flex-1 text-ink-2">{step.name}</span>
                <span className="font-mono text-ink-3">{step.ms} ms</span>
              </li>
            ))}
          </ol>
          {!status.running && (
            <p
              data-testid="reset-verdict"
              className={`mt-2 text-meta ${status.ok && !status.over_budget ? "text-emerald-600" : "text-rose-600"}`}
            >
              {status.ok ? t("demo.reset.ok") || "Reset complete" : t("demo.reset.failed") || "Reset failed"}
              {" · "}
              {status.seconds.toFixed(1)}s
              {status.over_budget ? ` · ${t("demo.reset.overBudget") || "over budget"}` : ""}
            </p>
          )}
        </>
      ) : (
        <p className="text-meta text-ink-3">
          {t("demo.reset.idle") || "No reset has run in this session."}
        </p>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- beat timer
function BeatTimer() {
  const { t } = useI18n();
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, [running]);

  const beat = BEATS.find((b) => elapsed >= b.startsAt && elapsed < b.endsAt) ?? BEATS[BEATS.length - 1];
  const target = beat ? beat.endsAt : 180;
  const drift = elapsed - target;

  return (
    <section className="rounded-md border border-edge bg-surface-1 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-meta font-semibold text-ink">{t("demo.timer.title") || "Rehearsal timer"}</h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setRunning(!running)}
            className="flex items-center gap-1.5 rounded-md border border-edge px-3 py-1.5 text-meta font-medium text-ink-2 hover:bg-surface-2"
          >
            {running ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            {running ? t("demo.timer.pause") || "Pause" : t("demo.timer.start") || "Start"}
          </button>
          <button
            type="button"
            onClick={() => {
              setRunning(false);
              setElapsed(0);
            }}
            className="rounded-md border border-edge px-3 py-1.5 text-meta font-medium text-ink-2 hover:bg-surface-2"
          >
            {t("demo.timer.reset") || "Reset"}
          </button>
        </div>
      </div>

      <p className="font-mono text-title text-ink" data-testid="beat-elapsed">
        {mmss(elapsed)} <span className="text-body text-ink-3">/ {mmss(180)}</span>
      </p>
      <p className="mt-1 text-meta text-ink-2" data-testid="beat-current">
        {t("demo.timer.beat") || "Beat"} {beat?.stage}: {beat?.title}
      </p>
      <p className={`text-micro ${drift > 0 ? "text-rose-600" : "text-ink-3"}`}>
        {drift > 0
          ? `${drift}s ${t("demo.timer.behind") || "behind this beat's target"}`
          : `${-drift}s ${t("demo.timer.left") || "left in this beat"}`}
      </p>
    </section>
  );
}

// ---------------------------------------------------------------- preflight
function PreflightPanel({
  checks,
  fetching,
  onRefresh,
}: {
  checks?: PreflightCheck[];
  fetching: boolean;
  onRefresh: () => void;
}) {
  const { t } = useI18n();
  const counts = {
    PASS: checks?.filter((c) => c.status === "PASS").length ?? 0,
    WARN: checks?.filter((c) => c.status === "WARN").length ?? 0,
    FAIL: checks?.filter((c) => c.status === "FAIL").length ?? 0,
  };

  return (
    <section className="rounded-md border border-edge bg-surface-1 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-meta font-semibold text-ink">
          {t("demo.preflight.title")}
          {checks ? (
            <span className="ml-2 font-normal text-ink-3">
              {counts.PASS} pass · {counts.WARN} warn · {counts.FAIL} fail
            </span>
          ) : null}
        </h2>
        <button
          type="button"
          onClick={onRefresh}
          disabled={fetching}
          className="flex items-center gap-1.5 rounded-md border border-edge px-3 py-1.5 text-meta font-medium text-ink-2 hover:bg-surface-2 disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${fetching ? "animate-spin" : ""}`} />
          {t("demo.preflight.rerun") || "Re-run"}
        </button>
      </div>

      {!checks ? (
        <p className="text-meta text-ink-3">
          {t("demo.preflight.unavailable") || "The demo engine did not answer, so nothing has been checked."}
        </p>
      ) : (
        <ul className="divide-y divide-edge">
          {checks.map((check) => (
            <li key={check.check} data-check={check.check} className="flex items-start gap-3 py-2">
              <span data-status={check.status} className="mt-0.5">
                {check.status === "PASS" ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                ) : check.status === "WARN" ? (
                  <AlertTriangle className="h-4 w-4 text-amber-500" />
                ) : (
                  <XCircle className="h-4 w-4 text-rose-500" />
                )}
              </span>
              <span className="w-40 shrink-0 text-meta font-medium text-ink">{check.check}</span>
              <span className="text-meta text-ink-2">{check.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- tamper lab
function TamperPanel({
  modes,
  active,
  onTamper,
  onUntamper,
  busy,
  result,
}: {
  modes: string[];
  active: { mode: string; event_uid: string; at: string }[];
  onTamper: (mode: string, eventUid?: string) => void;
  onUntamper: (eventUid?: string) => void;
  busy: boolean;
  result?: { target?: string; detail?: string; failed_steps?: string[] };
}) {
  const { t } = useI18n();
  const { scope } = useTenantScope();
  // Default to the last replayed event, which is what the presenter is looking at.
  const { data: results } = useLineageSearch("a.sharma", scope);
  const [eventUid, setEventUid] = useState("");
  const hits = results?.hits ?? [];
  const target = eventUid || active[0]?.event_uid || hits[0]?.event_uid || "";

  return (
    <section className="space-y-3 rounded-md border border-edge bg-surface-1 p-4">
      <div className="flex items-center gap-2">
        <ShieldAlert className="h-4 w-4 text-turmeric" />
        <h2 className="text-meta font-semibold text-ink">{t("demo.tamper.title")}</h2>
      </div>

      <label className="block text-meta text-ink-2" htmlFor="tamper-event">
        {t("demo.tamper.event") || "Target event"}
      </label>
      <select
        id="tamper-event"
        value={target}
        onChange={(event) => setEventUid(event.target.value)}
        className="h-9 w-full rounded-md border border-edge bg-surface-2 px-2 font-mono text-micro text-ink sm:w-[36rem]"
      >
        {hits.length === 0 && <option value="">{t("demo.tamper.noEvents") || "no event found"}</option>}
        {hits.map((hit) => (
          <option key={hit.event_uid} value={hit.event_uid}>
            {hit.event_uid} · {hit.raw_preview?.slice(0, 48)}
          </option>
        ))}
      </select>

      <div className="flex flex-wrap items-center gap-2">
        {modes.length === 0 && (
          <span className="text-meta text-ink-3">
            {t("demo.tamper.noModes") || "The tamper lab is unavailable (no sealed vault yet)."}
          </span>
        )}
        {modes.map((mode) => (
          <button
            key={mode}
            type="button"
            onClick={() => onTamper(mode, target || undefined)}
            disabled={busy}
            data-mode={mode}
            className="rounded-md border border-edge px-3 py-1.5 font-mono text-micro text-ink-2 hover:bg-surface-2 disabled:opacity-50"
          >
            {mode}
          </button>
        ))}
        <button
          type="button"
          onClick={() => onUntamper(target || undefined)}
          disabled={busy}
          className="flex items-center gap-1.5 rounded-md border border-edge px-3 py-1.5 text-meta font-medium text-ink-2 hover:bg-surface-2 disabled:opacity-50"
        >
          <Undo2 className="h-3.5 w-3.5" />
          {t("demo.tamper.untamper") || "Untamper"}
        </button>
      </div>

      {active.length > 0 && (
        <ul className="space-y-1 text-micro text-amber-600">
          {active.map((entry) => (
            <li key={`${entry.mode}:${entry.event_uid}`} className="flex items-center gap-1.5">
              <AlertTriangle className="h-3 w-3" />
              {entry.mode} {t("demo.tamper.on") || "on"} {entry.event_uid} ({entry.at})
            </li>
          ))}
        </ul>
      )}

      {result?.detail && (
        <p className="text-micro text-ink-2">
          <span className="text-ink-3">{result.target}: </span>
          {result.detail}
          {result.failed_steps?.length ? ` · ${t("demo.tamper.broke") || "broke"}: ${result.failed_steps.join(", ")}` : ""}
        </p>
      )}

      <p className="flex items-start gap-1.5 text-micro text-ink-3">
        <HelpCircle className="mt-0.5 h-3 w-3 shrink-0" />
        {t("demo.tamper.hint") ||
          "Shift+T on an event's Lineage page tampers it and re-verifies in one keystroke."}
      </p>
    </section>
  );
}
