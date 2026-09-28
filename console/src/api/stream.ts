import { ApiError } from "./client";

export interface SseFrame {
  event: string;
  data: unknown;
}

/** Split a text buffer into complete SSE frames; the incomplete tail is returned as `rest`. */
export function parseSseFrames(text: string): { frames: SseFrame[]; rest: string } {
  const normalized = text.replace(/\r\n/g, "\n");
  const parts = normalized.split("\n\n");
  const rest = parts.pop() ?? "";
  const frames: SseFrame[] = [];
  for (const part of parts) {
    let event = "message";
    const data: string[] = [];
    for (const line of part.split("\n")) {
      if (line.startsWith(":")) continue;
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) data.push(line.slice(6));
    }
    if (data.length === 0) continue;
    const raw = data.join("\n");
    let parsed: unknown = raw;
    try {
      parsed = JSON.parse(raw);
    } catch {
      /* keep the text */
    }
    frames.push({ event, data: parsed });
  }
  return { frames, rest };
}

/** POST a JSON body and hand each SSE frame of the streamed answer to `onEvent` (TC42). */
export async function postStream(
  path: string,
  body: unknown,
  onEvent: (frame: SseFrame) => void,
  signal?: AbortSignal,
): Promise<void> {
  // Absolute against our own origin, as the api client does: Node's fetch (tests) needs it.
  const response = await fetch(new URL(path, window.location.origin), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    credentials: "same-origin",
    signal,
  });
  if (!response.ok || !response.body) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, String((detail as { detail?: unknown }).detail ?? ""));
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const { frames, rest } = parseSseFrames(buffer + value);
    buffer = rest;
    frames.forEach(onEvent);
  }
  parseSseFrames(`${buffer}\n\n`).frames.forEach(onEvent);
}
