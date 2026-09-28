import { useSyncExternalStore } from "react";

export type SseState = "idle" | "connecting" | "open" | "retrying";

const RANK: Record<SseState, number> = { idle: 0, open: 1, connecting: 2, retrying: 3 };
const states = new Map<string, SseState>();
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

export function setStreamState(url: string, state: SseState): void {
  states.set(url, state);
  notify();
}

export function clearStream(url: string): void {
  if (states.delete(url)) notify();
}

/** The worst state across open streams; "idle" when there are none. */
export function overallState(): SseState {
  let worst: SseState = "idle";
  for (const state of states.values()) if (RANK[state] > RANK[worst]) worst = state;
  return worst;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useConnectionState(): SseState {
  return useSyncExternalStore(subscribe, overallState, overallState);
}
