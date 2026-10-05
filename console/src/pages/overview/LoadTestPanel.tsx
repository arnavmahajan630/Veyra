import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, CONTROL } from "../../api/client";
import { Drawer } from "../../components/Drawer";

export interface LoadTestPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface LoadStatus {
  running: boolean;
  sent: number;
  total: number;
}

export function LoadTestPanel({ open, onOpenChange }: LoadTestPanelProps) {
  const queryClient = useQueryClient();

  const [count, setCount] = useState(10000);
  const [eps, setEps] = useState(1000);
  const [mix, setMix] = useState("ssh");

  const status = useQuery({
    queryKey: ["load-status"],
    queryFn: () => api.get<LoadStatus>(`${CONTROL}/load/status`),
    refetchInterval: (query) => (query.state.data?.running ? 500 : false),
    enabled: open,
  });

  const startMutation = useMutation({
    mutationFn: () => api.post(`${CONTROL}/load/start`, { count, eps, mix }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["load-status"] }),
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
          Fire synthetic UDP syslog events directly into the pipeline to test throughput and stability.
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
              value={count}
              onChange={(e) => setCount(parseInt(e.target.value) || 0)}
              disabled={isRunning}
              className="rounded-control border border-rule bg-paper px-3 py-2 text-ink disabled:opacity-50 focus:outline-thread"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="load-eps" className="text-meta font-medium">
              Target EPS (Events per Second)
            </label>
            <input
              id="load-eps"
              type="number"
              min="1"
              value={eps}
              onChange={(e) => setEps(parseInt(e.target.value) || 0)}
              disabled={isRunning}
              className="rounded-control border border-rule bg-paper px-3 py-2 text-ink disabled:opacity-50 focus:outline-thread"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="load-mix" className="text-meta font-medium">
              Log Mix
            </label>
            <select
              id="load-mix"
              value={mix}
              onChange={(e) => setMix(e.target.value)}
              disabled={isRunning}
              className="rounded-control border border-rule bg-paper px-3 py-2 text-ink disabled:opacity-50 focus:outline-thread"
            >
              <option value="ssh">Linux SSH Logs</option>
              <option value="firewall">Firewall Logs</option>
            </select>
          </div>

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
                disabled={startMutation.isPending}
              >
                {startMutation.isPending ? "Starting..." : "Run Test"}
              </button>
            )}
          </div>
        </form>

        {status.data && (
          <div className="mt-4 border-t border-rule pt-4">
            <h3 className="text-meta font-medium mb-2">Test Status</h3>
            <div className="grid grid-cols-2 gap-4">
              <div className="flex flex-col">
                <span className="text-meta text-ink-2">State</span>
                <span className="font-medium">{isRunning ? "Running" : "Idle"}</span>
              </div>
              <div className="flex flex-col">
                <span className="text-meta text-ink-2">Progress</span>
                <span className="font-medium">
                  {status.data.sent.toLocaleString()} / {status.data.total.toLocaleString()}
                </span>
              </div>
            </div>
            {isRunning && (
              <div className="mt-4 h-2 w-full overflow-hidden rounded bg-rule">
                <div
                  className="h-full bg-thread transition-all duration-500"
                  style={{ width: `${(status.data.sent / status.data.total) * 100}%` }}
                />
              </div>
            )}
          </div>
        )}
      </div>
    </Drawer>
  );
}
