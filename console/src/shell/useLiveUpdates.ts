import { useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../api/queries";
import type { Overview } from "../api/types";
import { useToast } from "../components/Toast";
import { useI18n } from "../i18n/i18n";
import { useSSE, type EventSourceLike } from "../sse/useSSE";
import { useTenantScope } from "./tenant";

export const LINEAGE_STREAM = "/api/lineage/stream";
export const CONTROL_STREAM = "/api/control/stream";

/** Control events arrive as {type, data} (IF-API-CONTROL GET /stream); return `data`. */
function payload(event: unknown): Record<string, unknown> {
  if (typeof event === "object" && event !== null && "data" in event) {
    const data = (event as { data: unknown }).data;
    if (typeof data === "object" && data !== null) return data as Record<string, unknown>;
  }
  return {};
}

function text(data: Record<string, unknown>, key: string): string {
  const value = data[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "?";
}

/** One place turns both SSE streams into cache updates and toasts (C5 shell). */
export function useLiveUpdates(createSource?: (url: string) => EventSourceLike): void {
  const client = useQueryClient();
  const push = useToast();
  const { t } = useI18n();
  const { scope } = useTenantScope();
  const lineageUrl = scope ? `${LINEAGE_STREAM}?tenant=${encodeURIComponent(scope)}` : LINEAGE_STREAM;

  useSSE(
    lineageUrl,
    { overview: (data) => client.setQueryData<Overview>(queryKeys.overview(scope), data as Overview) },
    createSource,
  );

  useSSE(
    CONTROL_STREAM,
    {
      source: () => {
        void client.invalidateQueries({ queryKey: ["sources"] });
        void client.invalidateQueries({ queryKey: ["keys"] });
      },
      contract: (event) => {
        void client.invalidateQueries({ queryKey: ["contracts"] });
        push(t("toast.contract", { id: text(payload(event), "id") }));
      },
      drift: (event) => push(t("toast.drift", { source: text(payload(event), "source_id") })),
      replay: (event) => {
        const data = payload(event);
        if (data.status === "done") push(t("toast.replay", { job: text(data, "job_id") }), "success");
      },
    },
    createSource,
  );
}
