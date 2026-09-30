import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { AUTH_UID, OT_UID } from "../../mocks/lineageFixtures";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import LineagePage from "./LineagePage";

function renderPage(route = "/lineage") {
  signInAs("admin@veyra");
  return renderWithProviders(<LineagePage />, { route });
}

describe("LineagePage", () => {
  it("prompts for a search before showing anything", () => {
    renderPage();
    expect(screen.getByText(/Search by event UID/i)).toBeInTheDocument();
  });

  it("searches and links each hit to its event", async () => {
    renderPage();
    await userEvent.type(screen.getByRole("searchbox"), "a.sharma");
    await userEvent.keyboard("{Enter}");
    const link = await screen.findByRole("link", { name: /a\.sharma/ });
    expect(link).toHaveAttribute("href", `/lineage/${AUTH_UID}`);
  });

  it("actually filters: a term only the OT event carries returns only that event", async () => {
    renderPage();
    await userEvent.type(screen.getByRole("searchbox"), "ot-hist-01");
    await userEvent.keyboard("{Enter}");
    const links = await screen.findAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", `/lineage/${OT_UID}`);
  });

  it("says so when nothing matches", async () => {
    renderPage();
    await userEvent.type(screen.getByRole("searchbox"), "no-such-thing");
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText(/no events found/i)).toBeInTheDocument();
  });

  it("runs the query from ?q= on load, so a deep link works", async () => {
    renderPage("/lineage?q=103.21.4.77");
    expect(await screen.findByRole("link", { name: /a\.sharma/ })).toBeInTheDocument();
  });
});
