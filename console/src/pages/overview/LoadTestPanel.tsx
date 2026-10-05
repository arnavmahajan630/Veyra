import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, CONTROL } from "../../api/client";
import { Drawer } from "../../components/Drawer";

import { formatEps } from "../../components/format";

export interface LoadTestPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface LoadStatus {
  running: boolean;
  sent: number;
  total: number;
  outcome?: string | null;
  error?: string | null;
  actual_eps?: number;
}

export function LoadTestPanel({ open, onOpenChange }: LoadTestPanelProps) {
  const queryClient = useQueryClient();

  const [count, setCount] = useState<number | "">(5000000);

  const status = useQuery({
    queryKey: ["load-status"],
    queryFn: () => api.get<LoadStatus>(`${CONTROL}/load/status`),
    enabled: open,
  });

  const startMutation = useMutation({
    mutationFn: () => api.post(`${CONTROL}/load/start`, { count: Number(count) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["load-status"] }),
    onError: () => queryClient.invalidateQueries({ queryKey: ["load-status"] }),
  });

  const stopMutation = useMutation({
    mutationFn: () => api.post(`${CONTROL}/load/stop`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["load-status"] }),
  });

  const isRunning = status.data?.running ?? false;

  return (
    <Drawer open={open} onOpenChange={onOpenChange} title="Load Test Configuration">
      <div className="flex flex-col gap-6">
        <p className="text-meta text-ink-2">
          Fire synthetic envelopes directly into the Kafka raw topics using C-optimized bindings to stress test the system's maximum horizontal scaling capacity.
        </p>

        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!isRunning) startMutation.mutate();
          }}
        >
          <div className="flex flex-col gap-1">
            <label htmlFor="load-count" className="text-meta font-medium">
              Total Events
            </label>
            <input
              id="load-count"
              type="number"
              min="1"
              value={count ?? ""}
              onChange={(e) => setCount(e.target.value === "" ? "" : parseInt(e.target.value) || "")}
              disabled={isRunning}
              className="rounded-control border border-rule bg-paper px-3 py-2 text-ink disabled:opacity-50 focus:outline-thread"
            />
          </div>

          {startMutation.isError && (
            <div role="alert" className="text-bad text-meta font-medium">
              {(startMutation.error as Error).message}
            </div>
          )}

          <div className="mt-4 flex items-center gap-4">
            {isRunning ? (
              <button
                type="button"
                onClick={() => stopMutation.mutate()}
                className="w-full justify-center rounded-control border border-tier4 text-tier4 px-4 py-2 font-medium hover:bg-tier4 hover:text-paper disabled:opacity-50 transition-colors"
                disabled={stopMutation.isPending}
              >
                {stopMutation.isPending ? "Stopping..." : "Stop Test"}
              </button>
            ) : (
              <button
                type="submit"
                className="w-full justify-center rounded-control bg-thread text-paper px-4 py-2 font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
                disabled={startMutation.isPending || !count}
              >
                {startMutation.isPending ? "Starting..." : "Run Test"}
              </button>
            )}
          </div>
        </form>

        {status.data && (
          <div className="mt-4 border-t border-rule pt-4">
            <h3 className="text-meta font-medium mb-2">Test Status</h3>
            <div className="grid grid-cols-3 gap-4">
              <div className="flex flex-col">
                <span className="text-meta text-ink-2">State</span>
                <span className="font-medium">
                  {status.data.outcome === "failed" ? "Failed" : isRunning ? "Running" : "Idle"}
                </span>
              </div>
              <div className="flex flex-col">
                <span className="text-meta text-ink-2">Actual EPS</span>
                <span className="font-medium">
                  {status.data.actual_eps ? formatEps(status.data.actual_eps) : "0"}
                </span>
              </div>
              <div className="flex flex-col">
                <span className="text-meta text-ink-2">Progress</span>
                <span className="font-medium">
                  {status.data.sent.toLocaleString()} / {status.data.total.toLocaleString()}
                </span>
              </div>
            </div>
            
            {status.data.error && (
              <div role="alert" className="mt-4 rounded bg-bad/10 p-3 text-bad text-meta">
                {status.data.error}
              </div>
            )}

            {isRunning && (
              <div className="mt-4 h-2 w-full overflow-hidden rounded bg-rule">
                <div
                  className="h-full bg-thread transition-all duration-100 ease-linear"
                  style={{ width: `${(status.data.sent / Math.max(1, status.data.total)) * 100}%` }}
                />
              </div>
            )}
          </div>
        )}
      </div>
    </Drawer>
  );
}
