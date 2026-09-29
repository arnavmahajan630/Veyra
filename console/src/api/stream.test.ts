import { describe, expect, it } from "vitest";
import { parseSseFrames } from "./stream";

describe("parseSseFrames", () => {
  it("splits complete frames and keeps the partial rest", () => {
    const text = 'event: templates\ndata: [1]\n\nevent: library\ndata: {"matched":null}\n\nevent: dr';
    const { frames, rest } = parseSseFrames(text);
    expect(frames).toEqual([
      { event: "templates", data: [1] },
      { event: "library", data: { matched: null } },
    ]);
    expect(rest).toBe("event: dr");
  });

  it("ignores comments and tolerates CRLF", () => {
    const { frames } = parseSseFrames(': heartbeat\r\n\r\nevent: done\r\ndata: {"draft_id":"dr_1"}\r\n\r\n');
    expect(frames).toEqual([{ event: "done", data: { draft_id: "dr_1" } }]);
  });
});
