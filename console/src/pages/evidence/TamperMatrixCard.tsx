import { ShieldAlert } from "lucide-react";
import { useI18n } from "../../i18n/i18n";

export function TamperMatrixCard() {
  const { t } = useI18n();

  const rows = [
    {
      mode: "naive_flip",
      capability: "Write access to the vault segment file on disk",
      failsAt: "fetch_raw (AES-256-GCM tag mismatch)",
      diagnosis: "GCM ciphertext auth tag verification failed",
    },
    {
      mode: "insider_rewrite",
      capability: "Possesses the KEK, re-encrypts modified payload cleanly",
      failsAt: "merkle_inclusion",
      diagnosis: "Segment digest is not covered by the signed Merkle root",
    },
    {
      mode: "segment_delete",
      capability: "Filesystem write access, deletes sealed segment file",
      failsAt: "fetch_raw",
      diagnosis: "Sealed segment file is missing from vault directory",
    },
    {
      mode: "root_rewrite",
      capability: "Write access to the signed root ledger.jsonl file",
      failsAt: "root_signature",
      diagnosis: "Ed25519 digital signature validation failed against public key",
    },
  ];

  return (
    <div className="overflow-hidden rounded-md border border-edge bg-surface-1">
      <div className="flex items-center justify-between border-b border-edge bg-surface-2 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-turmeric" />
          <h3 className="text-meta font-semibold text-ink">
            {t("evidence.tamper.matrixTitle") || "Tamper Lab Attack Surface Matrix"}
          </h3>
        </div>
        <span className="text-micro text-ink-3">docs/tamper_matrix.md</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-meta">
          <thead className="border-b border-edge bg-surface-2/60 text-micro font-medium text-ink-3">
            <tr>
              <th className="px-4 py-2">Tamper Mode</th>
              <th className="px-4 py-2">Attacker Capability</th>
              <th className="px-4 py-2">First Failing Check</th>
              <th className="px-4 py-2">Locating Diagnostic</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-edge">
            {rows.map((row) => (
              <tr key={row.mode} className="hover:bg-surface-2/40 transition-colors">
                <td className="px-4 py-2.5 font-mono text-micro font-bold text-ink">
                  {row.mode}
                </td>
                <td className="px-4 py-2.5 text-micro text-ink-2">{row.capability}</td>
                <td className="px-4 py-2.5 font-mono text-micro text-rose-500 font-semibold">
                  {row.failsAt}
                </td>
                <td className="px-4 py-2.5 text-micro text-ink-3">{row.diagnosis}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
