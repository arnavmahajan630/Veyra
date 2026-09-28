import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Draft, DriftItem } from "../../api/types";
import { DRAFT_T3, DRIFT } from "../../mocks/fixtures";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import DriftDetailPage, { DEMO_FALLBACK_MS } from "./DriftDetailPage";

function renderDetail(email: string) {
  signInAs(email);
  return renderWithProviders(
    <Routes>
      <Route path="/drift/:id" element={<DriftDetailPage />} />
    </Routes>,
    { route: "/drift/dr_item_t3" },
  );
}

afterEach(() => {
  vi.useRealTimers();
});

describe("DriftDetailPage", () => {
  it("the author submits, then Approve shows the backend's refusal verbatim", async () => {
    renderDetail("author@maha");
    await userEvent.click(await screen.findByRole("button", { name: "Submit for approval" }));
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("this action needs one of the roles: admin, pack_approver");
  });

  it("the approver approves, promotes and replays the 8 events to the end", async () => {
    const author = renderDetail("author@maha");
    await userEvent.click(await screen.findByRole("button", { name: "Submit for approval" }));
    await screen.findByRole("button", { name: "Approve" });
    author.unmount();

    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderDetail("approver@veyra");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.click(await screen.findByRole("button", { name: "Promote" }));
    await userEvent.click(await screen.findByRole("button", { name: "Replay 8 events" }));
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    const done = await screen.findByRole("link", { name: "8 events replayed. View in Lineage" });
    expect(done.getAttribute("href")).toContain("q=t_3c85a1bfbf81");
  });

  it("in demo mode, a draft still drafting after 5 s is re-requested from the cache", async () => {
    const stuckItem: DriftItem = { ...(DRIFT[0] as DriftItem), state: "drafting", draft_id: "dr_stuck" };
    const stuck: Draft = { ...DRAFT_T3, draft_id: "dr_stuck", state: "drafting", templates: [], verification: null };
    const asked: unknown[] = [];
    server.use(
      http.get("/api/control/drift/dr_item_t3", () => HttpResponse.json(stuckItem)),
      http.get("/api/control/drafts/dr_stuck", () => HttpResponse.json(stuck)),
      http.post("/api/control/drift/dr_item_t3/draft", async ({ request }) => {
        asked.push(await request.json());
        return HttpResponse.json({ draft_id: "dr_stuck" }, { status: 202 });
      }),
    );
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderDetail("author@maha");
    await screen.findByText(/Drafting/);
    expect(asked).toEqual([]);
    await act(() => vi.advanceTimersByTimeAsync(DEMO_FALLBACK_MS + 100));
    expect(asked).toEqual([{ mode: "cache" }]);
  });

  it("offers Draft it when the item has no draft yet", async () => {
    const bare: DriftItem = { ...(DRIFT[0] as DriftItem), state: "open", draft_id: null };
    server.use(http.get("/api/control/drift/dr_item_t3", () => HttpResponse.json(bare)));
    renderDetail("author@maha");
    expect(await screen.findByRole("button", { name: "Draft it" })).toBeInTheDocument();
  });
});
