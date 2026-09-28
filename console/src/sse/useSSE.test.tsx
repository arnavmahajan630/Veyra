import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { overallState } from "./connection";
import { BACKOFF_START_MS, backoffDelay, useSSE, type EventSourceLike } from "./useSSE";

class FakeSource implements EventSourceLike {
  static made: FakeSource[] = [];
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  private listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();

  constructor(readonly url: string) {
    FakeSource.made.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
  }

  open(): void {
    this.onopen?.(new Event("open"));
  }

  fail(): void {
    this.onerror?.(new Event("error"));
  }

  emit(type: string, data: string): void {
    for (const listener of this.listeners.get(type) ?? []) listener(new MessageEvent(type, { data }));
  }
}

const create = (url: string) => new FakeSource(url);

beforeEach(() => {
  FakeSource.made = [];
});

afterEach(() => {
  vi.useRealTimers();
});

describe("backoffDelay", () => {
  it("doubles from 1 s and caps at 15 s", () => {
    expect([0, 1, 2, 3, 4, 10].map(backoffDelay)).toEqual([1000, 2000, 4000, 8000, 15000, 15000]);
  });
});

describe("useSSE", () => {
  it("delivers parsed JSON to the handler for its event name", () => {
    const onOverview = vi.fn();
    renderHook(() => useSSE("/api/lineage/stream", { overview: onOverview }, create));
    const source = FakeSource.made[0] as FakeSource;
    act(() => source.open());
    expect(overallState()).toBe("open");
    act(() => source.emit("overview", '{"eps_1m": 17}'));
    expect(onOverview).toHaveBeenCalledWith({ eps_1m: 17 });
  });

  it("passes non-JSON data through as a string", () => {
    const onPing = vi.fn();
    renderHook(() => useSSE("/s", { ping: onPing }, create));
    act(() => (FakeSource.made[0] as FakeSource).emit("ping", "hello"));
    expect(onPing).toHaveBeenCalledWith("hello");
  });

  it("reconnects with backoff after an error", () => {
    vi.useFakeTimers();
    renderHook(() => useSSE("/s", {}, create));
    const first = FakeSource.made[0] as FakeSource;
    act(() => first.fail());
    expect(first.closed).toBe(true);
    expect(overallState()).toBe("retrying");
    act(() => vi.advanceTimersByTime(BACKOFF_START_MS));
    expect(FakeSource.made).toHaveLength(2);
    act(() => (FakeSource.made[1] as FakeSource).open());
    expect(overallState()).toBe("open");
  });

  it("closes the stream and forgets its state on unmount", () => {
    const { unmount } = renderHook(() => useSSE("/s", {}, create));
    unmount();
    expect((FakeSource.made[0] as FakeSource).closed).toBe(true);
    expect(overallState()).toBe("idle");
  });

  it("stays idle where the browser has no EventSource", () => {
    renderHook(() => useSSE("/s", {}));
    expect(overallState()).toBe("idle");
  });
});
