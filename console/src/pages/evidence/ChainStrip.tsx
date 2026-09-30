// The last 30 windows as linked beads. A bead or link turns red from the ledger-audit
// flags the API attaches to each root, so a broken chain shows where it broke.

import { Link2 } from "lucide-react";
import { useEvidenceRoots } from "../../api/queries";
import type { LedgerRoot } from "../../api/types";
import { useI18n } from "../../i18n/i18n";

const MAX_BEADS = 30;

type Bead = "ok" | "broken" | "unknown";

export function beadState(root: LedgerRoot): Bead {
  if (root.chain_ok === true) return "ok";
  if (root.chain_ok === false) return "broken";
  if (root.signature_ok === undefined && root.prev_link_ok === undefined) return "unknown";
  return root.signature_ok !== false && root.prev_link_ok !== false ? "ok" : "broken";
}

export function ChainStrip() {
  const { t } = useI18n();
  const { data } = useEvidenceRoots(MAX_BEADS);
  // Oldest on the left, so the chain reads in the direction it was built.
  const roots = [...(data?.roots ?? [])].slice(0, MAX_BEADS).reverse();
  const status = data?.audit_status ?? "unknown";

  const pill =
    status === "PASS"
      ? { className: "bg-emerald-500/10 text-emerald-600", label: t("evidence.chain.ok") || "Chain intact" }
      : status === "FAIL"
        ? { className: "bg-rose-500/10 text-rose-600", label: t("evidence.chain.broken") || "Chain broken" }
        : { className: "bg-surface-3 text-ink-3", label: t("evidence.chain.unknown") || "Audit unavailable" };

  return (
    <div className="space-y-3 rounded-md border border-edge bg-surface-1 p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Link2 className="h-4 w-4 text-turmeric" />
          <h3 className="text-meta font-semibold text-ink">
            {t("evidence.chain.title") || "Window integrity chain (last 30 windows)"}
          </h3>
        </div>
        <span
          data-testid="chain-status"
          className={`rounded px-2 py-0.5 text-micro font-medium ${pill.className}`}
        >
          {pill.label}
        </span>
      </div>

      <div className="flex items-center gap-1.5 overflow-x-auto pb-1">
        {roots.length === 0 ? (
          <p className="text-micro text-ink-3">
            {t("evidence.chain.awaiting") || "Awaiting the first window seal…"}
          </p>
        ) : (
          roots.map((root, index) => {
            const state = beadState(root);
            const color =
              state === "ok" ? "bg-emerald-500" : state === "broken" ? "bg-rose-500" : "bg-ink-3/40";
            // The link into a bead is what the prev-hash check covers, so a broken
            // prev link colours the link, not just the bead.
            const linkBroken = root.prev_link_ok === false;
            return (
              <div key={root.window_id} className="flex shrink-0 items-center gap-1">
                {index > 0 && (
                  <span
                    aria-hidden="true"
                    className={`h-0.5 w-2 ${linkBroken ? "bg-rose-500" : "bg-emerald-500/60"}`}
                  />
                )}
                <span
                  data-window={root.window_id}
                  data-state={state}
                  title={`${root.window_id}: ${state}`}
                  className={`h-4 w-4 cursor-pointer rounded-full transition-transform hover:scale-125 ${color}`}
                />
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
