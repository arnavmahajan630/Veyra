import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "../i18n/i18n";
import { tiers } from "../mocks/fixtures";
import { Kbd } from "./Kbd";
import { MixBar } from "./MixBar";
import { StatusDot } from "./StatusDot";
import { TierBar } from "./TierBar";
import { TierChip } from "./TierChip";
import { percent, tierShares, tierTotal } from "./tiers";

const wrap = (node: React.ReactNode) => render(<I18nProvider>{node}</I18nProvider>);

describe("tier maths", () => {
  it("totals and shares", () => {
    const counts = tiers(90, 5, 4, 1);
    expect(tierTotal(counts)).toBe(100);
    expect(tierShares(counts)).toEqual({ 1: 0.9, 2: 0.05, 3: 0.04, 4: 0.01 });
    expect(percent(0.904)).toBe("90%");
  });

  it("treats an empty window as all zero, not NaN", () => {
    expect(tierShares(tiers(0, 0, 0, 0))).toEqual({ 1: 0, 2: 0, 3: 0, 4: 0 });
  });
});

describe("tier components", () => {
  it("TierChip shows the translated label", () => {
    wrap(<TierChip tier={3} />);
    expect(screen.getByText("Unknown template")).toBeInTheDocument();
  });

  it("MixBar describes every share for screen readers", () => {
    wrap(<MixBar counts={tiers(62, 0, 38, 0)} />);
    expect(screen.getByRole("img")).toHaveAccessibleName(
      "Match 62%, Partial 0%, Unknown template 38%, Unparseable 0%",
    );
  });

  it("TierBar draws one column per sample", () => {
    const { container } = wrap(<TierBar series={[tiers(1, 0, 0, 0), tiers(1, 1, 0, 0), tiers(0, 0, 1, 1)]} />);
    expect(container.querySelectorAll("g[data-sample]")).toHaveLength(3);
  });

  it("TierBar with a capacity keeps the newest sample at the right edge", () => {
    const { container } = wrap(<TierBar series={[tiers(1, 0, 0, 0), tiers(0, 1, 0, 0)]} capacity={10} />);
    expect(container.querySelector("svg")?.getAttribute("viewBox")).toBe("0 0 10 100");
    const columns = [...container.querySelectorAll("g[data-sample]")];
    expect(columns.map((g) => g.querySelector("rect")?.getAttribute("x"))).toEqual(["8", "9"]);
  });

  it("StatusDot and Kbd carry their text", () => {
    wrap(
      <>
        <StatusDot tone="warn" label="Reconnecting" />
        <Kbd>?</Kbd>
      </>,
    );
    expect(screen.getByText("Reconnecting")).toBeInTheDocument();
    expect(screen.getByText("?").tagName).toBe("KBD");
  });
});
