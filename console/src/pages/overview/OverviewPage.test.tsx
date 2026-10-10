import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes, useLocation } from "react-router";
import { describe, expect, it } from "vitest";
import { queryKeys } from "../../api/queries";
import { overviewAt } from "../../mocks/fixtures";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import OverviewPage from "./OverviewPage";

function Where() {
  const location = useLocation();
  return <p>{`${location.pathname}${location.search}`}</p>;
}

function renderPage() {
  signInAs("admin@veyra");
  return renderWithProviders(
    <Routes>
      <Route path="/" element={<OverviewPage />} />
      <Route path="/sources" element={<Where />} />
      <Route path="/onboard" element={<Where />} />
    </Routes>,
  );
}

describe("OverviewPage", () => {
  it("draws the pipeline with live numbers", async () => {
    renderPage();
    const flow = await screen.findByRole("figure", { name: "Pipeline" });
    expect(within(flow).getAllByText("15 events/s")).toHaveLength(4);
    expect(within(flow).getByText("900/min")).toBeInTheDocument();
    expect(flow.querySelectorAll("[data-stage]")).toHaveLength(8);
    expect(within(flow).getByText("40 segments")).toBeInTheDocument();
    expect(flow.querySelector('[data-stage="wazuh"]')).toHaveTextContent("Lag 0 s");
  });

  it("names each source in the strip, with its id beneath", async () => {
    renderPage();
    const strip = await screen.findByRole("region", { name: "Sources" });
    expect(await within(strip).findByText("Acme NGFW (DMZ)")).toBeInTheDocument();
    expect(within(strip).getByText("src_fw_dmz_01")).toBeInTheDocument();
  });

  it("opens the tier bar full (90 ten-second columns) when the server sends its history", async () => {
    const { container } = renderPage();
    await screen.findByRole("figure", { name: "Pipeline" });
    expect(container.querySelectorAll("g[data-sample]")).toHaveLength(90);
  });

  it("updates when a new snapshot lands in the cache (the SSE path)", async () => {
    server.use(http.get("/api/lineage/overview", () => HttpResponse.json(overviewAt(0))));
    const { client, container } = renderPage();
    await screen.findByRole("figure", { name: "Pipeline" });
    act(() => {
      client.setQueryData(queryKeys.overview(null), overviewAt(3, Date.now() + 1_000));
    });
    expect(await screen.findAllByText("18 events/s")).toHaveLength(4);
    expect(container.querySelectorAll("g[data-sample]")).toHaveLength(1);
  });

  it("shows evidence and delivery status", async () => {
    renderPage();
    expect(await screen.findByText("Last segment sealed 7s ago")).toBeInTheDocument();
    expect(screen.getByText(/Last signed root w_1790496000/)).toBeInTheDocument();
    expect(screen.getByText("Anchored in immudb")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Chain intact" })).toHaveAttribute("href", "/evidence");
    expect(within(screen.getByRole("region", { name: "Delivery" })).getByText("Lag 0 s")).toBeInTheDocument();
  });

  it("opens the source drawer from the sources strip", async () => {
    renderPage();
    await userEvent.click(await screen.findByText("src_fw_dmz_01"));
    expect(await screen.findByText("/sources?source=src_fw_dmz_01")).toBeInTheDocument();
  });

  it("says the chain is not audited rather than broken when the verdict is unknown", async () => {
    // The server sends null when the ledger audit could not run. Treating that as false
    // painted "Chain broken" in red on an intact ledger, right at the start of the demo.
    server.use(
      http.get("/api/lineage/overview", () => {
        const base = overviewAt(0);
        return HttpResponse.json({ ...base, vault: { ...base.vault, chain_ok: null } });
      }),
    );
    renderPage();
    expect(await screen.findByRole("link", { name: "Chain not audited" })).toBeInTheDocument();
    expect(screen.queryByText("Chain broken")).not.toBeInTheDocument();
  });

  it("invites onboarding when there are no sources", async () => {
    server.use(
      http.get("/api/lineage/overview", () => HttpResponse.json({ ...overviewAt(0), sources: [] })),
    );
    renderPage();
    const invite = await screen.findByRole("link", { name: "No sources yet. Onboard your first source." });
    expect(invite).toHaveAttribute("href", "/onboard");
  });
});
