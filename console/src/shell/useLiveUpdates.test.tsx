import { act, renderHook, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { queryKeys } from "../api/queries";
import type { Overview } from "../api/types";
import { overviewAt } from "../mocks/fixtures";
import type { EventSourceLike } from "../sse/useSSE";
import { makeQueryClient, Providers } from "../test/render";
import { CONTROL_STREAM, LINEAGE_STREAM, useLiveUpdates } from "./useLiveUpdates";

class FakeSource implements EventSourceLike {
  static made: FakeSource[] = [];
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  private listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();

  constructor(readonly url: string) {
    FakeSource.made.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {}

  emit(type: string, data: unknown): void {
    const event = new MessageEvent(type, { data: JSON.stringify(data) });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

const stream = (url: string) => FakeSource.made.find((s) => s.url === url) as FakeSource;

function setup() {
  const client = makeQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => <Providers client={client}>{children}</Providers>;
  renderHook(() => useLiveUpdates((url) => new FakeSource(url)), { wrapper });
  return client;
}

beforeEach(() => {
  FakeSource.made = [];
});

describe("useLiveUpdates", () => {
  it("opens the lineage and control streams", () => {
    setup();
    expect(FakeSource.made.map((s) => s.url).sort()).toEqual([CONTROL_STREAM, LINEAGE_STREAM]);
  });

  it("writes overview ticks into the query cache", () => {
    const client = setup();
    act(() => stream(LINEAGE_STREAM).emit("overview", overviewAt(5)));
    expect(client.getQueryData<Overview>(queryKeys.overview(null))?.eps_1m).toBe(16);
  });

  it("refreshes sources and keys when a source changes", () => {
    const client = setup();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    act(() => stream(CONTROL_STREAM).emit("source", { type: "source", data: { event: "key_issued" } }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sources"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["keys"] });
  });

  it("toasts contract changes, new drift and finished replays only", () => {
    setup();
    const control = stream(CONTROL_STREAM);
    act(() => control.emit("contract", { type: "contract", data: { id: "authsrv" } }));
    act(() => control.emit("drift", { type: "drift", data: { source_id: "src_authsrv_01", created: true } }));
    act(() => control.emit("replay", { type: "replay", data: { job_id: "rp_1", status: "running" } }));
    act(() => control.emit("replay", { type: "replay", data: { job_id: "rp_1", status: "done" } }));
    const toasts = screen.getByRole("status");
    expect(toasts).toHaveTextContent("Contract authsrv changed");
    expect(toasts).toHaveTextContent("New message shape on src_authsrv_01");
    expect(toasts.textContent?.match(/Replay rp_1 finished/g)).toHaveLength(1);
  });

  it("refreshes the inbox for every drift event but toasts only new items", () => {
    const client = setup();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    act(() => stream(CONTROL_STREAM).emit("drift", { type: "drift", data: { source_id: "src_authsrv_01", created: false } }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["drift"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["driftItem"] });
    expect(screen.queryByText(/New message shape/)).not.toBeInTheDocument();
  });

  it("reads a draft event through its {type, data} wrapper", () => {
    const client = setup();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    act(() => stream(CONTROL_STREAM).emit("draft", { type: "draft", data: { draft_id: "dr_t3", state: "ready" } }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.draft("dr_t3") });
  });

  it("writes replay progress from the event into the job's query", () => {
    const client = setup();
    const job = { job_id: "rp_1", contract_id: "authsrv", state: "normalizing", total: 8, normalized: 3 };
    client.setQueryData(queryKeys.replay("rp_1"), job);
    act(() =>
      stream(CONTROL_STREAM).emit("replay", { type: "replay", data: { job_id: "rp_1", status: "done", normalized: 8 } }),
    );
    expect(client.getQueryData(queryKeys.replay("rp_1"))).toMatchObject({ state: "done", normalized: 8 });
  });
});
