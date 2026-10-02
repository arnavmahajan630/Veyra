import { useRef } from "react";
import { useDemoStage, useMe, useTriggerStage } from "../../api/queries";
import { useHotkey } from "../../hotkeys/useHotkey";
import { useI18n } from "../../i18n/i18n";

/**
 * A hotkey pressed twice in quick succession is one intent, not two (B7 AC3). The engine is
 * idempotent while a stage runs, but a stage that finishes fast would otherwise re-run on
 * the second tap of a fumbled keypress.
 */
export const STAGE_COOLDOWN_MS = 1_500;

/**
 * Shift+1..6 trigger the demo's stages.
 *
 * Mounted in the shell, not on the demo panel: `04_DEMO_SCRIPT.md` promises the stage hotkeys
 * work from any console page, and the presenter spends most of the demo on Overview, Drift and
 * Lineage. Registered only in demo mode, so nothing fires on a normal deployment.
 *
 * Shift+R stays on /demo, because a reset asks for confirmation first, and Shift+T stays on
 * the event view, because it tampers whichever event is open.
 */
export function useStageHotkeys(): void {
  const { t } = useI18n();
  const { data: me } = useMe();
  const demoMode = Boolean(me?.demo_mode);
  const triggerStage = useTriggerStage();
  const { data: stageStatus } = useDemoStage(demoMode);
  const lastRun = useRef<Record<number, number>>({});

  for (const number of [1, 2, 3, 4, 5, 6]) {
    // One registration per stage; `when` keeps them inert outside demo mode.
    useHotkey({
      combo: `Shift+${number}`,
      description: t("demo.hotkey.stage", { n: String(number) }) || `Run demo stage ${number}`,
      when: () => demoMode,
      handler: () => {
        if (triggerStage.isPending) return;
        if (stageStatus?.stage === number && stageStatus.state === "running") return;
        const now = Date.now();
        if (now - (lastRun.current[number] ?? 0) < STAGE_COOLDOWN_MS) return;
        lastRun.current[number] = now;
        triggerStage.mutate(number);
      },
    });
  }
}
