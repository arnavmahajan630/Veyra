import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { api, CONTROL } from "../../api/client";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import OnboardPage from "./OnboardPage";

describe("OnboardPage", () => {
  it("walks source → samples → analysis → draft, one section at a time", async () => {
    signInAs("author@maha");
    renderWithProviders(<OnboardPage />, { route: "/onboard" });
    expect(screen.queryByLabelText("Samples")).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Name"), "Auth Server");
    await userEvent.selectOptions(screen.getByLabelText("Transport"), "http_push");
    await userEvent.click(screen.getByRole("button", { name: "Create source" }));
    await userEvent.click(await screen.findByRole("button", { name: "Paste samples" }));
    await userEvent.click(screen.getByRole("button", { name: "Analyze" }));
    expect(await screen.findByText(/syslog → json \(msg\) → text/)).toBeInTheDocument();
    expect(await screen.findAllByRole("table")).toHaveLength(2); // one compact review per template
    expect(screen.getByRole("button", { name: "Create contract" })).toBeEnabled();
  });

  it("the author sees the approver hint; the approver's own draft is refused with four-eyes", async () => {
    signInAs("author@maha");
    renderWithProviders(<OnboardPage />, { route: "/onboard?draft=dr_onboard" });
    await userEvent.click(await screen.findByRole("button", { name: "Create contract" }));
    expect(await screen.findByText("Switch to approver@veyra to approve")).toBeInTheDocument();
  });

  it("the approver approves and activates; then the key step issues a key once", async () => {
    signInAs("author@maha");
    const first = renderWithProviders(<OnboardPage />, { route: "/onboard?draft=dr_onboard" });
    await userEvent.click(await screen.findByRole("button", { name: "Create contract" }));
    await screen.findByText("Switch to approver@veyra to approve");
    first.unmount();

    signInAs("approver@veyra");
    renderWithProviders(<OnboardPage />, { route: "/onboard?draft=dr_onboard&contract=auth_server&version=1" });
    await userEvent.click(await screen.findByRole("button", { name: "Approve and activate" }));
    expect(await screen.findByText("auth_server v1 is active")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Issue key" })).toBeInTheDocument();
  });

  it("a version that has since been retired shows its state, not an endless Loading", async () => {
    signInAs("author@maha");
    await api.post(`${CONTROL}/drafts/dr_t3/submit`);
    signInAs("approver@veyra");
    await api.post(`${CONTROL}/contracts/authsrv/versions/2/approve`);
    await api.post(`${CONTROL}/contracts/authsrv/versions/2/promote`);
    renderWithProviders(<OnboardPage />, { route: "/onboard?contract=authsrv&version=1" });
    expect(await screen.findByText("authsrv v1: Retired")).toBeInTheDocument();
  });

  it("an approver approving their own submission sees the four-eyes refusal verbatim (AC3)", async () => {
    signInAs("admin@veyra");
    renderWithProviders(<OnboardPage />, { route: "/onboard?draft=dr_onboard" });
    await userEvent.click(await screen.findByRole("button", { name: "Create contract" }));
    await userEvent.click(await screen.findByRole("button", { name: "Approve and activate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "four-eyes: admin@veyra submitted auth_server@1, so someone else must approve it",
    );
  });
});
