import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Overview } from "../../api/types";
import { overviewAt, tiers } from "../../mocks/fixtures";
import { strokeWidth } from "./PipelineFlow";
import { SERIES_LENGTH, bucketSeries, recordOverview, tierDelta, useTierSeries } from "./useTierSeries";

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

  it("survives leaving the page and coming back", () => {
    const first = renderHook(({ overview }) => useTierSeries(overview), {
      initialProps: { overview: overviewAt(0, T0) as Overview | undefined },
    });
    first.rerender({ overview: overviewAt(1, T0 + 1_000) });
    first.unmount();
    const again = renderHook(() => useTierSeries(overviewAt(1, T0 + 1_000)));
    expect(again.result.current).toEqual([tiers(13, 1, 1, 0)]);
  });

  it("keeps collecting from the live stream while another page is open", () => {
    recordOverview(null, overviewAt(0, T0));
    recordOverview(null, overviewAt(1, T0 + 1_000));
    const { result } = renderHook(() => useTierSeries(undefined));
    expect(result.current).toHaveLength(1);
  });

  it("takes the server's history when a snapshot carries one", () => {
    const history = Array.from({ length: SERIES_LENGTH + 5 }, () => tiers(2, 0, 1, 0));
    const { result, rerender } = renderHook(({ overview }) => useTierSeries(overview), {
      initialProps: { overview: { ...overviewAt(0, T0), tier_history: history } as Overview | undefined },
    });
    expect(result.current).toHaveLength(SERIES_LENGTH);
    rerender({ overview: overviewAt(1, T0 + 1_000) });
    expect(result.current).toHaveLength(SERIES_LENGTH);
    expect(result.current.at(-1)).toEqual(tiers(13, 1, 1, 0));
  });

  it("keeps one series per tenant scope", () => {
    recordOverview("t_ntro_core", { ...overviewAt(0, T0), tier_history: [tiers(1, 0, 0, 0)] });
    const all = renderHook(() => useTierSeries(undefined, null));
    const ntro = renderHook(() => useTierSeries(undefined, "t_ntro_core"));
    expect(all.result.current).toEqual([]);
    expect(ntro.result.current).toEqual([tiers(1, 0, 0, 0)]);
  });
});

describe("bucketSeries", () => {
  const one = tiers(1, 0, 0, 0);

  it("sums samples into ten-second columns", () => {
    const series = Array.from({ length: 25 }, () => one);
    expect(bucketSeries(series, 25)).toEqual([tiers(10, 0, 0, 0), tiers(10, 0, 0, 0), tiers(5, 0, 0, 0)]);
  });

  it("keeps column boundaries still as the window slides, so only the newest column changes", () => {
    const before = bucketSeries(Array.from({ length: 20 }, () => one), 25);
    const after = bucketSeries(Array.from({ length: 20 }, () => one), 26);
    expect(before).toEqual([tiers(5, 0, 0, 0), tiers(10, 0, 0, 0), tiers(5, 0, 0, 0)]);
    expect(after).toEqual([tiers(4, 0, 0, 0), tiers(10, 0, 0, 0), tiers(6, 0, 0, 0)]);
  });
});
