import { Archive } from "lucide-react";
import type { VaultLocation as VaultLocationType } from "../../api/types";
import { useI18n } from "../../i18n/i18n";

export interface VaultLocationProps {
  vault?: VaultLocationType | null;
}

export function VaultLocation({ vault }: VaultLocationProps) {
  const { t } = useI18n();

  if (!vault) return null;

  return (
    <div className="rounded-md border border-edge bg-surface-1 p-4 space-y-2">
      <div className="flex items-center gap-2">
        <Archive className="h-4 w-4 text-turmeric" />
        <h3 className="text-meta font-semibold text-ink">
          {t("lineage.event.vault") || "Vault Location"}
        </h3>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-meta">
        <div>
          <span className="text-micro text-ink-3 block">Segment</span>
          <span className="font-mono text-ink-2 font-medium truncate block" title={vault.segment_id}>
            {vault.segment_id}
          </span>
        </div>
        <div>
          <span className="text-micro text-ink-3 block">Record index</span>
          <span className="font-mono text-ink-2 font-medium">{vault.record_idx}</span>
        </div>
        <div>
          <span className="text-micro text-ink-3 block">Sealed at</span>
          <span className="font-mono text-ink-2">
            {vault.sealed_at ? new Date(vault.sealed_at).toLocaleTimeString() : "Pending"}
          </span>
        </div>
        <div>
          <span className="text-micro text-ink-3 block">Window</span>
          <span className="font-mono text-ink-2 font-medium truncate block" title={vault.window_id ?? ""}>
            {vault.window_id || "Unsigned"}
          </span>
        </div>
      </div>
    </div>
  );
}
