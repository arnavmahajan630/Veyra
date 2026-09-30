// The signed-root ledger, seeded from /evidence/roots and live-appended from the
// `root` SSE event the evidence API emits as each window is signed.
//
// The signature / prev-link columns read the ledger-audit flags the API attaches. When the
// audit could not run the flags are absent and the cell says "unknown" — this table never
// draws a tick it did not get from the server.

import { AlertTriangle, CheckCircle2, Clock, HelpCircle, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { useEvidenceRoots } from "../../api/queries";
import type { LedgerRoot } from "../../api/types";
import { useI18n } from "../../i18n/i18n";
import { useSSE, type EventSourceLike } from "../../sse/useSSE";

function isLedgerRoot(value: unknown): value is LedgerRoot {
  return (
    !!value &&
    typeof value === "object" &&
    typeof (value as LedgerRoot).window_id === "string" &&
    typeof (value as LedgerRoot).payload === "object"
  );
}

export interface LedgerTableProps {
  /** Injected in tests, the way `useLiveUpdates` takes one; the app uses EventSource. */
  createSource?: (url: string) => EventSourceLike;
}

export function LedgerTable({ createSource }: LedgerTableProps = {}) {
  const { t } = useI18n();
  const { data: initialData, isLoading } = useEvidenceRoots(50);
  const [roots, setRoots] = useState<LedgerRoot[]>([]);

  useEffect(() => {
    if (initialData?.roots) setRoots(initialData.roots);
  }, [initialData]);

  // The handler map is keyed by SSE event name; the server emits `overview` and `root`.
  useSSE(
    "/api/lineage/stream",
    {
      root: (data: unknown) => {
        if (!isLedgerRoot(data)) return;
        setRoots((prev) => [data, ...prev.filter((r) => r.window_id !== data.window_id)]);
      },
    },
    createSource,
  );

  if (isLoading && roots.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center rounded-md border border-edge bg-surface-1">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-turmeric border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-md border border-edge bg-surface-1">
      <div className="flex items-center justify-between border-b border-edge bg-surface-2 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-turmeric" />
          <h2 className="text-meta font-semibold text-ink">
            {t("evidence.roots.title") || "Signed roots ledger"}
          </h2>
        </div>
        <span className="font-mono text-micro text-ink-3">
          {roots.length} {t("evidence.roots.entries") || "windows"}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-meta">
          <thead className="border-b border-edge bg-surface-2/60 text-micro font-medium text-ink-3">
            <tr>
              <th className="px-4 py-2">{t("evidence.roots.window") || "Window"}</th>
              <th className="px-4 py-2">{t("evidence.roots.range") || "Time range"}</th>
              <th className="px-4 py-2 text-right">{t("evidence.roots.leaves") || "Leaves"}</th>
              <th className="px-4 py-2">{t("evidence.roots.root") || "Merkle root (SHA-256)"}</th>
              <th className="px-4 py-2 text-center">
                {t("evidence.roots.signature") || "Signature"}
              </th>
              <th className="px-4 py-2 text-center">{t("evidence.roots.prev") || "Prev link"}</th>
              <th className="px-4 py-2 text-center">{t("evidence.roots.immudb") || "immudb"}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-edge">
            {roots.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-ink-3">
                  {t("evidence.roots.empty") || "No window has been signed yet."}
                </td>
              </tr>
            ) : (
              roots.map((root) => {
                const payload = root.payload || {};
                const start = payload.window_start
                  ? new Date(Number(payload.window_start) * 1000).toLocaleTimeString()
                  : "";
                const end = payload.window_end
                  ? new Date(Number(payload.window_end) * 1000).toLocaleTimeString()
                  : "";
                const leafCount = payload.leaf_count ?? "—";
                const rootHex = String(payload.merkle_root_sha256 || payload.root || "");

                return (
                  <tr
                    key={root.window_id}
                    data-window={root.window_id}
                    className="transition-colors hover:bg-surface-2/40"
                  >
                    <td className="px-4 py-2.5 font-mono text-micro font-medium text-ink">
                      {root.window_id}
                    </td>
                    <td className="px-4 py-2.5 text-micro text-ink-2">
                      {start && end ? `${start} – ${end}` : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-micro text-ink-2">
                      {String(leafCount)}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-micro text-ink-2" title={rootHex}>
                      {rootHex ? `${rootHex.slice(0, 10)}…${rootHex.slice(-8)}` : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-center">
                      <AuditBadge ok={root.signature_ok} okLabel="Ed25519" />
                    </td>
                    <td className="px-4 py-2.5 text-center">
                      <AuditBadge
                        ok={root.prev_link_ok}
                        okLabel={t("evidence.roots.linked") || "linked"}
                      />
                    </td>
                    <td className="px-4 py-2.5 text-center">
                      {/* immudb anchoring is a prototype in this build; the verify step
                          says the same thing, so the badge must not imply otherwise. */}
                      <span className="inline-flex items-center gap-1 rounded bg-surface-3 px-1.5 py-0.5 text-micro text-ink-3">
                        <Clock className="h-3 w-3" />
                        {t("evidence.roots.prototype") || "prototype"}
                      </span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AuditBadge({ ok, okLabel }: { ok?: boolean; okLabel: string }) {
  const { t } = useI18n();
  if (ok === undefined) {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-surface-3 px-1.5 py-0.5 text-micro text-ink-3">
        <HelpCircle className="h-3 w-3" />
        {t("evidence.roots.unknown") || "unknown"}
      </span>
    );
  }
  if (!ok) {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-rose-500/10 px-1.5 py-0.5 text-micro font-medium text-rose-600">
        <AlertTriangle className="h-3 w-3" />
        {t("evidence.roots.broken") || "broken"}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded bg-emerald-500/10 px-1.5 py-0.5 text-micro font-medium text-emerald-600">
      <CheckCircle2 className="h-3 w-3" />
      {okLabel}
    </span>
  );
}
