import { CheckCircle2, Clock, Send, XCircle } from "lucide-react";
import type { ReceiptRow } from "../../api/types";
import { useI18n } from "../../i18n/i18n";

export interface DeliveriesListProps {
  receipts: ReceiptRow[];
  currentRevision: number;
}

export function DeliveriesList({ receipts, currentRevision }: DeliveriesListProps) {
  const { t } = useI18n();

  if (receipts.length === 0) {
    return null;
  }

  return (
    <div className="rounded-md border border-edge bg-surface-1 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Send className="h-4 w-4 text-turmeric" />
        <h3 className="text-meta font-semibold text-ink">
          {t("lineage.event.deliveries") || "Deliveries"}
        </h3>
      </div>

      <div className="flex flex-wrap gap-2">
        {receipts.map((r, i) => {
          const isCurrent = r.revision === currentRevision;
          const isDelivered = r.status === "delivered";
          const isFiltered = r.status === "filtered";

          return (
            <div
              key={`${r.route_id}-${r.revision}-${i}`}
              className={`flex items-center gap-2 rounded border px-2.5 py-1 text-meta ${
                isCurrent
                  ? "border-edge bg-surface-2 text-ink font-medium"
                  : "border-edge bg-surface-1 text-ink-3"
              }`}
            >
              {isDelivered ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
              ) : isFiltered ? (
                <Clock className="h-3.5 w-3.5 text-ink-3" />
              ) : (
                <XCircle className="h-3.5 w-3.5 text-rose-500" />
              )}
              <span className="font-semibold">{r.route_id}</span>
              <span className="font-mono text-micro text-ink-3">rev {r.revision}</span>
              <span className="text-micro text-ink-2">{r.status}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
