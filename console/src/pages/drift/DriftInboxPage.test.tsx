import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes, useLocation } from "react-router";
import { describe, expect, it } from "vitest";
import type { DriftItem } from "../../api/types";
import { DRIFT } from "../../mocks/fixtures";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import DriftInboxPage from "./DriftInboxPage";

function Where() {
  const location = useLocation();
  return <p>{`${location.pathname}${location.search}`}</p>;
}

function renderInbox(route = "/drift") {
  signInAs("author@maha");
  return renderWithProviders(
    <Routes>
      <Route path="/drift" element={<><DriftInboxPage /><Where /></>} />
      <Route path="/drift/:id" element={<Where />} />
    </Routes>,
    { route },
  );
}

describe("DriftInboxPage", () => {
  it("shows the T3 card with its drain template and state", async () => {
    renderInbox();
    const card = await screen.findByRole("link", { name: /user=<\*> FAILED login/ });
    expect(within(card).getByText("draft ready")).toBeInTheDocument();
    expect(within(card).getByText("8 events")).toBeInTheDocument();
    expect(card).toHaveAttribute("href", "/drift/dr_item_t3");
  });

  it("shows the empty state under Resolved", async () => {
    renderInbox();
    await screen.findByRole("link", { name: /FAILED login/ });
    await userEvent.click(screen.getByRole("button", { name: "Resolved" }));
    expect(await screen.findByText("Nothing here. New message shapes appear on their own.")).toBeInTheDocument();
  });

  it("filters to one source from the Sources drawer's link, and can show all again", async () => {
    const asked: (string | null)[] = [];
    server.events.on("request:start", ({ request }) => {
      const url = new URL(request.url);
      if (url.pathname === "/api/control/drift") asked.push(url.searchParams.get("source_id"));
    });
    renderInbox("/drift?source=src_authsrv_01");
    await screen.findByRole("link", { name: /FAILED login/ });
    expect(asked).toContain("src_authsrv_01");
    expect(screen.getByText("Only src_authsrv_01")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show all sources" }));
    expect(await screen.findByText("/drift")).toBeInTheDocument();
    server.events.removeAllListeners();
  });

  it("marks an item that arrives while the page is open", async () => {
    const { client } = renderInbox();
    await screen.findByRole("link", { name: /FAILED login/ });
    const fresh: DriftItem = { ...(DRIFT[0] as DriftItem), drift_id: "dr_item_new", drain_template: "session <*> reset by <*>", state: "open", draft_id: null };
    server.use(http.get("/api/control/drift", () => HttpResponse.json([fresh, DRIFT[0]])));
    await act(() => client.invalidateQueries({ queryKey: ["drift"] }));
    const card = await screen.findByRole("link", { name: /session <\*> reset by/ });
    expect(card).toHaveAttribute("data-new", "true");
    expect(screen.getByRole("link", { name: /FAILED login/ })).not.toHaveAttribute("data-new");
  });
});
