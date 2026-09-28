import { useState } from "react";
import { useLifecycle } from "../../api/queries";
import type { ContractDetail, VersionDetail } from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useI18n } from "../../i18n/i18n";

type Pending = { kind: "approve" | "promote" } | { kind: "rollback"; to: number };

const BUTTON = "rounded-control border border-thread px-3 py-1 text-thread disabled:border-rule disabled:text-ink-2";

/**
 * Approve, promote and roll back, each behind a confirm that says what happens (C6). The buttons
 * show to everyone who can see the contract: the backend decides, and its refusal (a role or
 * four-eyes 403) is shown verbatim beside the buttons (C6 AC3).
 */
export function LifecycleActions({ contract, version }: { contract: ContractDetail; version: VersionDetail }) {
  const { t } = useI18n();
  const lifecycle = useLifecycle(contract.id);
  const [pending, setPending] = useState<Pending | null>(null);
  const [done, setDone] = useState<string[]>([]);
  const [refusal, setRefusal] = useState<string | null>(null);
  const id = contract.id;
  const v = version.version;
  const canary = version.state === "canary";
  // Any version that was once active can be restored; a finished rollback keeps its (past tense) button.
  const rollbacks = contract.versions.filter(
    (x) => (x.state === "retired" && x.promoted_at !== null) || done.includes(`rollback-${x.version}`),
  );

  const run = async () => {
    if (!pending) return;
    setRefusal(null);
    try {
      if (pending.kind === "approve") await lifecycle.approve.mutateAsync(v);
      if (pending.kind === "promote") await lifecycle.promote.mutateAsync(v);
      if (pending.kind === "rollback") await lifecycle.rollback.mutateAsync(pending.to);
      setDone((current) => [...current, pending.kind === "rollback" ? `rollback-${pending.to}` : pending.kind]);
    } catch (error) {
      setRefusal(error instanceof Error ? error.message : String(error));
    } finally {
      setPending(null);
    }
  };

  const dialog =
    pending?.kind === "approve"
      ? { title: t("contracts.approveTitle", { id, v }), body: t("contracts.approveBody", { id, v }), label: t("contracts.approve") }
      : pending?.kind === "promote"
        ? {
            title: t("contracts.promoteTitle", { id, v }),
            body:
              contract.active_version === null
                ? t("contracts.promoteBodyFirst", { id, v })
                : t("contracts.promoteBody", { id, v, old: contract.active_version }),
            label: t("contracts.promote"),
          }
        : pending?.kind === "rollback"
          ? {
              title: t("contracts.rollbackTitle", { id, v: pending.to }),
              body: t("contracts.rollbackBody", { id, v: pending.to }),
              label: t("contracts.rollbackTo", { v: pending.to }),
            }
          : null;
  const busy = lifecycle.approve.isPending || lifecycle.promote.isPending || lifecycle.rollback.isPending;

  return (
    <div className="flex flex-wrap items-center gap-3">
      {canary && (version.approved_by === null || done.includes("approve")) ? (
        <button type="button" disabled={done.includes("approve")} onClick={() => setPending({ kind: "approve" })} className={BUTTON}>
          {done.includes("approve") ? t("contracts.approved") : t("contracts.approve")}
        </button>
      ) : null}
      {(canary && version.approved_by !== null) || done.includes("promote") ? (
        <button type="button" disabled={done.includes("promote")} onClick={() => setPending({ kind: "promote" })} className={BUTTON}>
          {done.includes("promote") ? t("contracts.promoted") : t("contracts.promote")}
        </button>
      ) : null}
      {rollbacks.map((x) => {
        const finished = done.includes(`rollback-${x.version}`);
        return (
          <button
            key={x.version}
            type="button"
            disabled={finished}
            onClick={() => setPending({ kind: "rollback", to: x.version })}
            className={BUTTON}
          >
            {finished ? t("contracts.rolledBackTo", { v: x.version }) : t("contracts.rollbackTo", { v: x.version })}
          </button>
        );
      })}
      {refusal ? (
        <p role="alert" className="text-meta">
          {refusal}
        </p>
      ) : null}
      <ConfirmDialog
        open={dialog !== null}
        title={dialog?.title ?? ""}
        body={dialog?.body ?? ""}
        confirmLabel={dialog?.label ?? ""}
        onConfirm={() => void run()}
        onCancel={() => setPending(null)}
        busy={busy}
      />
    </div>
  );
}
