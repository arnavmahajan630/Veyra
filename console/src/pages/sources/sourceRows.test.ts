import { describe, expect, it } from "vitest";
import type { Source, SourceHealth } from "../../api/types";
import { SOURCES, healthAt } from "../../mocks/fixtures";
import { SILENT_AFTER_MS, buildRows, epsDelta, formatSkew, silentFor } from "./sourceRows";

const NOW = Date.parse("2026-09-27T10:00:00Z");
const source = SOURCES[0] as Source;
const seenAgo = (ms: number): SourceHealth => ({
  ...(healthAt(NOW)[0] as SourceHealth),
  last_seen: new Date(NOW - ms).toISOString(),
});

describe("silentFor", () => {
  it("is null while events keep arriving", () => {
    expect(silentFor(source, seenAgo(2_000), NOW)).toBeNull();
    expect(silentFor(source, seenAgo(SILENT_AFTER_MS), NOW)).toBeNull();
  });

  it("reports how long a source has been quiet once it passes 60 s", () => {
    expect(silentFor(source, seenAgo(80_000), NOW)).toBe(80_000);
  });

  it("measures a never-seen source from its registration", () => {
    const never = { ...seenAgo(0), last_seen: null };
    expect(silentFor(source, never, NOW)).toBe(3_600_000);
    expect(silentFor(source, undefined, NOW)).toBe(3_600_000);
  });

  it("ignores sources that expect no traffic or are paused", () => {
    expect(silentFor({ ...source, expected_eps: 0 }, { ...seenAgo(80_000), expected_eps: 0 }, NOW)).toBeNull();
    expect(silentFor({ ...source, status: "paused" }, seenAgo(80_000), NOW)).toBeNull();
  });
});

describe("buildRows", () => {
  it("keeps registry order, joins health by id and drops unknown health rows", () => {
    const stray = { ...(healthAt(NOW)[0] as SourceHealth), source_id: "src_gone" };
    const rows = buildRows(SOURCES, [...healthAt(NOW), stray], NOW);
    expect(rows.map((r) => [r.source.id, r.health?.source_id])).toEqual([
      ["src_fw_dmz_01", "src_fw_dmz_01"],
      ["src_lnx_core_07", "src_lnx_core_07"],
    ]);
  });

  it("flags nothing while health data is unknown", () => {
    expect(buildRows(SOURCES, undefined, NOW).every((r) => r.silentMs === null)).toBe(true);
  });
});

describe("cell formats", () => {
  it("epsDelta compares actual with expected", () => {
    expect(epsDelta(6.4, 6)).toBe("+7%");
    expect(epsDelta(0, 9)).toBe("-100%");
    expect(epsDelta(6, 6)).toBe("±0%");
    expect(epsDelta(3, 0)).toBeNull();
  });

  it("formatSkew prints milliseconds or a dash", () => {
    expect(formatSkew(-812)).toBe("-812 ms");
    expect(formatSkew(null)).toBe("—");
    expect(formatSkew(undefined)).toBe("—");
  });
});
