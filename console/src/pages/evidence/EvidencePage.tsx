import { useI18n } from "../../i18n/i18n";
import { ChainStrip } from "./ChainStrip";
import { ExportPanel } from "./ExportPanel";
import { LedgerTable } from "./LedgerTable";
import { PublicKeyPanel } from "./PublicKeyPanel";
import { TamperMatrixCard } from "./TamperMatrixCard";

export default function EvidencePage() {
  const { t } = useI18n();

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-title font-semibold text-ink">
          {t("evidence.title") || "Evidence & Integrity"}
        </h1>
        <p className="mt-1 text-body text-ink-3">
          {t("evidence.description") ||
            "Cryptographic proof ledger, Merkle inclusion roots, and offline verification packages."}
        </p>
      </div>

      <ChainStrip />
      <LedgerTable />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ExportPanel />
        <PublicKeyPanel />
      </div>

      <TamperMatrixCard />
    </div>
  );
}
