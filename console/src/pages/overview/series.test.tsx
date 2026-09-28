import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Overview } from "../../api/types";
import { overviewAt, tiers } from "../../mocks/fixtures";
import { strokeWidth } from "./PipelineFlow";
import { SERIES_LENGTH, tierDelta, useTierSeries } from "./useTierSeries";

const T0 = Date.parse("2026-09-27T10:00:00Z");

describe("strokeWidth", () => {
  it("grows with throughput on a log scale, from 1 px to 6 px", () => {
    expect(strokeWidth(0)).toBe(1);
    expect(strokeWidth(9)).toBe(3);
    expect(strokeWidth(1_000_000)).toBe(6);
  });
});

describe("tierDelta", () => {
  it("counts events per tier since the last snapshot and never goes negative", () => {
    expect(tierDelta(tiers(10, 5, 0, 1), tiers(15, 5, 3, 0))).toEqual(tiers(5, 0, 3, 0));
  });
});

describe("useTierSeries", () => {
  it("adds one sample per new snapshot and ignores a repeated one", () => {
    const { result, rerender } = renderHook(({ overview }) => useTierSeries(overview), {
      initialProps: { overview: overviewAt(0, T0) as Overview | undefined },
    });
    expect(result.current).toEqual([]);
    rerender({ overview: overviewAt(1, T0 + 1_000) });
    expect(result.current).toEqual([tiers(13, 1, 1, 0)]);
    rerender({ overview: { ...overviewAt(1, T0 + 1_000) } });
    expect(result.current).toHaveLength(1);
  });

  it("keeps the last 15 minutes of samples", () => {
    expect(SERIES_LENGTH).toBe(15 * 60);
  });
});
