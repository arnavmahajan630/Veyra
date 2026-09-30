import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { AUTH_RAW, AUTH_UID, OT_UID } from "../../mocks/lineageFixtures";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import { EventDetailView } from "./EventDetailView";

function renderDetail(uid = AUTH_UID) {
  signInAs("admin@veyra");
  return renderWithProviders(<EventDetailView uid={uid} />, { route: `/lineage/${uid}` });
}

async function rowFor(path: string) {
  return await screen.findByRole("option", { name: new RegExp(path.replace(/\./g, "\\.")) });
}

/** The text of every highlighted span currently drawn in the raw pane. */
function highlighted(): string[] {
  return Array.from(document.querySelectorAll("mark[data-span-id]")).map(
    (mark) => mark.textContent ?? "",
  );
}

async function waitForHighlight(value: string) {
  await waitFor(() => expect(highlighted()).toContain(value));
}

describe("EventDetailView", () => {
  it("shows both revisions and selects the newest", async () => {
    renderDetail();
    expect((await screen.findAllByText(/rev 2/)).length).toBeGreaterThan(0);
    // rev 2 is tier 1 under authsrv@2, which is what the timeline must say.
    expect(screen.getByText("authsrv@2")).toBeInTheDocument();
  });

  it("highlights exactly the bytes of a hovered field", async () => {
    renderDetail();
    const row = await rowFor("src_endpoint.ip");
    await userEvent.hover(row);
    // The raw pane marks the span; its text must be the field's value, nothing more.
    await waitForHighlight("103.21.4.77");
  });

  it("highlights a JSON-escaped value inside the embedded body", async () => {
    renderDetail();
    await userEvent.hover(await rowFor("user.name"));
    await waitForHighlight("a.sharma");
  });

  it("highlights a multi-byte Devanagari value at the right characters", async () => {
    renderDetail(OT_UID);
    await userEvent.hover(await rowFor("unmapped"));
    // A byte span read as a char span would slice into the wrong place here.
    await waitForHighlight("चेतावनी");
  });

  it("moves through fields with the arrow keys and pins with Enter", async () => {
    renderDetail();
    const first = await rowFor("user.name");
    first.focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(await rowFor("src_endpoint.ip")).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(await rowFor("src_endpoint.ip")).toHaveAttribute("aria-selected", "true");
  });

  it("shows the diff between revisions when asked", async () => {
    renderDetail();
    await userEvent.click(await screen.findByRole("button", { name: /diff/i }));
    const diff = await screen.findByText(/rev 1 → rev 2/);
    expect(diff).toBeInTheDocument();
  });

  it("says so plainly when the index is unavailable instead of inventing a revision", async () => {
    server.use(
      http.get("/api/lineage/events/:uid", () =>
        HttpResponse.json({
          event_uid: AUTH_UID,
          raw: { raw_text: AUTH_RAW, raw_len: 177, raw_sha256: "42d8", raw_preview: "…" },
          revisions: [],
          index_available: false,
          receipts: [],
        }),
      ),
    );
    renderDetail();
    expect(await screen.findByText(/lineage index is unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/No normalized revision/i)).toBeInTheDocument();
    // Nothing invented: no OCSF row at all.
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
  });
});
