import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DRAFT_T3 } from "../../mocks/fixtures";
import { renderWithProviders } from "../../test/render";
import { BacktestStrip } from "./BacktestStrip";
import { YamlView } from "./YamlView";

describe("YamlView", () => {
  it("colours each key and keeps the rest of the line as ink", () => {
    render(<YamlView yaml={"contract: authsrv\n  - id: auth_ok\nplain line"} />);
    expect(screen.getByText("contract")).toHaveClass("text-thread");
    expect(screen.getByText(": authsrv")).toHaveClass("text-ink");
    expect(screen.getByText("plain line")).toBeInTheDocument();
  });
});

describe("BacktestStrip", () => {
  it("shows the backtest's error instead of its numbers", () => {
    renderWithProviders(<BacktestStrip backtest={{ ...DRAFT_T3.backtest!, error: "evidence-api is down" }} sig="t_1" />);
    expect(screen.getByRole("alert")).toHaveTextContent("evidence-api is down");
    expect(screen.queryByText(/Tier 4 → tier 1/)).not.toBeInTheDocument();
  });

  it("links to the lineage search for the template", () => {
    renderWithProviders(<BacktestStrip backtest={DRAFT_T3.backtest!} sig="t_3c85a1bfbf81" />);
    expect(screen.getByRole("link", { name: "View events" })).toHaveAttribute("href", "/lineage?q=t_3c85a1bfbf81");
  });
});
