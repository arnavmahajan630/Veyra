import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { api, CONTROL } from "../../api/client";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import ContractDetailPage from "./ContractDetailPage";

/** authsrv@2 as Beat 4 creates it: the T3 draft submitted by `email`. */
async function submitV2(email = "author@maha") {
  signInAs(email);
  await api.post(`${CONTROL}/drafts/dr_t3/submit`);
}

function renderPage(email: string, route = "/contracts/authsrv") {
  signInAs(email);
  return renderWithProviders(
    <Routes>
      <Route path="/contracts/:id" element={<ContractDetailPage />} />
    </Routes>,
    { route },
  );
}

async function confirm(action: string) {
  await userEvent.click(await screen.findByRole("button", { name: action }));
  const dialog = await screen.findByRole("dialog");
  await userEvent.click(within(dialog).getByRole("button", { name: action }));
}

describe("ContractDetailPage", () => {
  it("a version that doesn't exist says so instead of loading forever", async () => {
    renderPage("approver@veyra", "/contracts/authsrv?v=99");
    expect(await screen.findByRole("alert")).toHaveTextContent("authsrv@99 not found");
  });

  it("shows each history actor in full", async () => {
    renderPage("approver@veyra");
    const actors = await screen.findAllByText("approver@veyra", { selector: "li span" });
    for (const actor of actors) expect(actor).not.toHaveClass("truncate");
  });

  it("shows the history grouped by version", async () => {
    renderPage("approver@veyra");
    const entry = (await screen.findByText("author@maha")).closest("li") as HTMLElement;
    expect(within(entry).getByText("Submitted")).toBeInTheDocument();
  });

  it("shows the author the backend's refusal verbatim when they try to approve", async () => {
    await submitV2();
    renderPage("author@maha");
    await confirm("Approve");
    expect(await screen.findByRole("alert")).toHaveTextContent("this action needs one of the roles: admin, pack_approver");
  });

  it("refuses an approver's own submission with the four-eyes message (AC3)", async () => {
    await submitV2("admin@veyra");
    renderPage("admin@veyra");
    await confirm("Approve");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "four-eyes: admin@veyra submitted authsrv@2, so someone else must approve it",
    );
  });

  it("approves, then explains what promoting does before doing it", async () => {
    await submitV2();
    renderPage("approver@veyra");
    await confirm("Approve");
    expect(await screen.findByRole("button", { name: "Approved" })).toBeDisabled();
    await userEvent.click(await screen.findByRole("button", { name: "Promote" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("switches within 1 second");
  });

  it("offers a rollback to each version that was once active", async () => {
    await submitV2();
    signInAs("approver@veyra");
    await api.post(`${CONTROL}/contracts/authsrv/versions/2/approve`);
    await api.post(`${CONTROL}/contracts/authsrv/versions/2/promote`);
    renderPage("approver@veyra");
    await confirm("Roll back to v1");
    expect(await screen.findByRole("button", { name: "Rolled back to v1" })).toBeDisabled();
  });

  it("shows the semantic diff before the YAML diff", async () => {
    await submitV2();
    renderPage("approver@veyra");
    await userEvent.click(await screen.findByRole("tab", { name: "Diff" }));
    const added = await screen.findByRole("heading", { name: "Templates added" });
    const template = screen.getByText("logon_failed", { selector: "li" });
    const yaml = screen.getByLabelText("YAML diff");
    expect(added.compareDocumentPosition(template) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(template.compareDocumentPosition(yaml) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
