import { useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../api/queries";
import type { LedgerRoot, LedgerRootsResponse, Overview, ReplayJob } from "../api/types";
import { useToast } from "../components/Toast";
import { recordOverview } from "../pages/overview/useTierSeries";
import { useI18n } from "../i18n/i18n";
import { useSSE, type EventSourceLike } from "../sse/useSSE";
import { useTenantScope } from "./tenant";

export const LINEAGE_STREAM = "/api/lineage/stream";
/** The `limit` values the app asks `/evidence/roots` for; live roots are merged into each. */
const ROOT_LIMITS = [50] as const;
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
    {
      overview: (data) => {
        client.setQueryData<Overview>(queryKeys.overview(scope), data as Overview);
        recordOverview(scope, data as Overview);
      },
      // A newly signed root. Handled here rather than in the Evidence page, which used to
      // open a second EventSource to this same endpoint just for this one event.
      root: (data) => {
        const root = data as LedgerRoot;
        if (!root || typeof root.window_id !== "string") return;
        for (const limit of ROOT_LIMITS) {
          client.setQueryData<LedgerRootsResponse>(queryKeys.roots(limit), (prev) =>
            prev === undefined
              ? prev
              : {
                  ...prev,
                  count: prev.count + (prev.roots.some((r) => r.window_id === root.window_id) ? 0 : 1),
                  roots: [root, ...prev.roots.filter((r) => r.window_id !== root.window_id)].slice(
                    0,
                    limit,
                  ),
                },
          );
        }
      },
    },
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
      drift: (event) => {
        void client.invalidateQueries({ queryKey: ["drift"] });
        void client.invalidateQueries({ queryKey: ["driftItem"] });
        const data = payload(event) as { source_id?: string; created?: boolean };
        if (data.created) push(t("toast.drift", { source: data.source_id ?? "" }));
      },
      draft: (event) => {
        const data = payload(event) as { draft_id?: string };
        if (data.draft_id) void client.invalidateQueries({ queryKey: queryKeys.draft(data.draft_id) });
      },
      replay: (event) => {
        const job = payload(event) as Partial<ReplayJob> & { job_id?: string; status?: string };
        if (job.job_id) client.setQueryData(queryKeys.replay(job.job_id), (old: ReplayJob | undefined) =>
          old ? { ...old, ...job, state: (job.status ?? old.state) as ReplayJob["state"] } : old);
        if (job.status === "done") push(t("toast.replay", { job: job.job_id ?? "?" }), "success");
      },
      load: (event) => {
        client.setQueryData(["load-status"], payload(event));
      },
    },
    createSource,
  );
}
