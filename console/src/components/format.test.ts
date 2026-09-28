import { describe, expect, it } from "vitest";
import { ageMs, formatAge, formatEps, parseTime } from "./format";

describe("formatAge", () => {
  it.each<[number, string]>([
    [0, "0s"],
    [12_400, "12s"],
    [80_000, "1m 20s"],
    [3_900_000, "1h 5m"],
    [-5_000, "0s"],
  ])("%i ms reads as %s", (ms, text) => {
    expect(formatAge(ms)).toBe(text);
  });
});

describe("timestamps", () => {
  it("reads nanosecond ISO timestamps", () => {
    expect(parseTime("2026-09-27T09:00:00.123456789Z")).toBe(Date.parse("2026-09-27T09:00:00.123Z"));
  });

  it("measures an age, or returns null without a readable timestamp", () => {
    const now = Date.parse("2026-09-27T09:00:01.123Z");
    expect(ageMs("2026-09-27T09:00:00.123456789Z", now)).toBe(1000);
    expect(ageMs(null, now)).toBeNull();
    expect(ageMs("yesterday", now)).toBeNull();
  });
});

describe("formatEps", () => {
  it("keeps one decimal below 100 and drops a trailing .0", () => {
    expect(formatEps(6.43)).toBe("6.4");
    expect(formatEps(6)).toBe("6");
    expect(formatEps(1234.5)).toBe("1235");
  });
});
