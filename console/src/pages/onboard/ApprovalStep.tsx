import { useState } from "react";
import { useContractVersion, useLifecycle, useMe } from "../../api/queries";
import { useI18n } from "../../i18n/i18n";

const APPROVERS = new Set(["admin", "pack_approver"]);

/**
 * Step 5: four-eyes. The approver gets Approve and activate (approve, then promote); the author
 * waits, with a hint in demo mode. Any refusal is shown verbatim beside the button (C6 AC3).
 */
export function ApprovalStep({ contractId, version }: { contractId: string; version: number }) {
  const { t } = useI18n();
  const me = useMe().data;
  const row = useContractVersion(contractId, version);
  const lifecycle = useLifecycle(contractId);
  const [refusal, setRefusal] = useState<string | null>(null);
  const state = row.data?.state;

  const activate = async () => {
    setRefusal(null);
    try {
      if (!row.data?.approved_by) await lifecycle.approve.mutateAsync(version);
      await lifecycle.promote.mutateAsync(version);
    } catch (error) {
      setRefusal(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <section aria-labelledby="onboard-approval">
      <h2 id="onboard-approval" className="text-lead font-semibold">
        {t("onboard.approval")}
      </h2>
      {state === "active" ? (
        <p className="mt-1">{t("onboard.active", { id: contractId, v: version })}</p>
      ) : state === "canary" ? (
        me && APPROVERS.has(me.role) ? (
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={lifecycle.approve.isPending || lifecycle.promote.isPending}
              onClick={() => void activate()}
              className="rounded-control bg-thread px-3 py-1.5 text-paper disabled:opacity-50"
            >
              {t("onboard.approveActivate")}
            </button>
            {refusal ? (
              <p role="alert" className="text-meta">
                {refusal}
              </p>
            ) : null}
          </div>
        ) : (
          <div className="mt-1">
            <p>{t("onboard.waiting")}</p>
            {me?.demo_mode ? <p className="text-meta text-ink-2">{t("onboard.switchHint")}</p> : null}
          </div>
        )
      ) : row.error ? (
        <p role="alert">{row.error.message}</p>
      ) : state ? (
        // Retired or rolled back since: say so rather than wait for a canary that won't come back.
        <p className="mt-1">
          {t("drift.versionState", { id: contractId, v: version, state: t(`contracts.state.${state}`) })}
        </p>
      ) : (
        <p className="text-ink-2">{t("common.loading")}</p>
      )}
    </section>
  );
}
