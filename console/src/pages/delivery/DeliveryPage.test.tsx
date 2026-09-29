import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { overviewAt } from "../../mocks/fixtures";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import DeliveryPage from "./DeliveryPage";

const rowOf = async (text: string) => (await screen.findByText(text)).closest("tr") as HTMLElement;

describe("DeliveryPage", () => {
  it("joins the route definitions with their live statistics", async () => {
    signInAs("admin@veyra");
    renderWithProviders(<DeliveryPage />, { route: "/delivery" });
    const wazuh = await rowOf("wazuh_main");
    expect(wazuh).toHaveTextContent("ndjson_file /sinks/wazuh/veyra.ndjson");
    expect(await rowOf("partner_masked")).toHaveTextContent("user.name: hmac");
    expect(screen.getByText(/Recent receipts arrive with evidence-api's receipts listing/)).toBeInTheDocument();
  });

  it("shows an open breaker in the bad tone", async () => {
    const overview = overviewAt(0);
    server.use(
      http.get("/api/lineage/overview", () =>
        HttpResponse.json({ ...overview, routes: overview.routes.map((r) => ({ ...r, breaker: r.route_id === "wazuh_main" ? "open" : r.breaker })) }),
      ),
    );
    signInAs("admin@veyra");
    renderWithProviders(<DeliveryPage />, { route: "/delivery" });
    const wazuh = await rowOf("wazuh_main");
    const label = await screen.findByText("Open");
    expect(wazuh).toContainElement(label);
    expect(label.previousElementSibling).toHaveClass("bg-tier4");
  });
});
