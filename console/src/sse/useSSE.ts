import { useEffect, useRef } from "react";
import { clearStream, setStreamState } from "./connection";

export const BACKOFF_START_MS = 1_000;
export const BACKOFF_MAX_MS = 15_000;

export interface EventSourceLike {
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void;
  close(): void;
}

export type SseHandlers = Record<string, (data: unknown) => void>;
type SourceFactory = (url: string) => EventSourceLike;

const browserSource: SourceFactory = (url) => new EventSource(url, { withCredentials: true });

export function backoffDelay(attempt: number): number {
  return Math.min(BACKOFF_START_MS * 2 ** attempt, BACKOFF_MAX_MS);
}

function parse(data: string): unknown {
  try {
    return JSON.parse(data) as unknown;
  } catch {
    return data;
  }
}

/** Follow an SSE stream; reconnect with capped exponential backoff after errors. */
export function useSSE(url: string | null, handlers: SseHandlers, createSource: SourceFactory = browserSource): void {
  const latest = useRef(handlers);
  useEffect(() => {
    latest.current = handlers;
  });

  useEffect(() => {
    if (!url) return;
    if (createSource === browserSource && typeof EventSource === "undefined") return;

    let source: EventSourceLike | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let stopped = false;

    const connect = () => {
      setStreamState(url, attempt === 0 ? "connecting" : "retrying");
      const current = createSource(url);
      source = current;
      current.onopen = () => {
        attempt = 0;
        setStreamState(url, "open");
      };
      current.onerror = () => {
        current.close();
        if (stopped) return;
        setStreamState(url, "retrying");
        timer = setTimeout(connect, backoffDelay(attempt));
        attempt += 1;
      };
      for (const type of Object.keys(latest.current)) {
        current.addEventListener(type, (event) => latest.current[type]?.(parse(event.data)));
      }
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      source?.close();
      clearStream(url);
    };
  }, [url, createSource]);
}
