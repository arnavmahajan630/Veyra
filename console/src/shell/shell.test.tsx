import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { resetMockState, signInAs } from "../mocks/handlers";
import { renderWithProviders } from "../test/render";
import { AppShell } from "./AppShell";
import { createQueryClient } from "./queryClient";

function renderShell(route = "/") {
  return renderWithProviders(<AppShell />, { route, client: createQueryClient() });
}

const pageTitle = (name: string) => screen.findByRole("heading", { level: 1, name });

describe("the shell", () => {
  it("asks for a login, then shows the Overview", async () => {
    renderShell();
    await pageTitle("Sign in to VEYRA");
    await userEvent.type(screen.getByLabelText("Email"), "admin@veyra");
    await userEvent.type(screen.getByLabelText("Password"), "veyra-demo");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await pageTitle("Overview");
    expect(screen.getByRole("link", { name: "Sources" })).toBeInTheDocument();
  });

  it("says so when the password is wrong", async () => {
    renderShell();
    await pageTitle("Sign in to VEYRA");
    await userEvent.type(screen.getByLabelText("Email"), "admin@veyra");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password. Try again.");
  });

  it("returns to the login page when the session expires", async () => {
    signInAs("admin@veyra");
    const { client } = renderShell("/sources");
    await screen.findByText("Acme NGFW (DMZ)");
    resetMockState();
    await act(() => client.invalidateQueries({ queryKey: ["sources"] }));
    await pageTitle("Sign in to VEYRA");
  });

  it("builds the nav from the role and demo mode", async () => {
    signInAs("approver@veyra");
    renderShell();
    await pageTitle("Overview");
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(within(nav).queryByRole("link", { name: "Onboard source" })).toBeNull();
    expect(within(nav).getByRole("link", { name: "Demo" })).toBeInTheDocument();
  });

  it("switches demo users in one click (AC5)", async () => {
    signInAs("author@maha");
    renderShell();
    await userEvent.click(await screen.findByRole("button", { name: "Switch to approver@veyra" }));
    expect(await screen.findByRole("button", { name: "Switch to author@maha" })).toBeInTheDocument();
    expect(screen.getByText("approver@veyra")).toBeInTheDocument();
  });

  it("switches the shell and Overview to Hindi (AC4)", async () => {
    signInAs("admin@veyra");
    renderShell();
    await pageTitle("Overview");
    await userEvent.selectOptions(screen.getByLabelText("Language"), "hi");
    await pageTitle("अवलोकन");
    expect(screen.getByRole("link", { name: "स्रोत" })).toBeInTheDocument();
  });

  it("lets a platform user scope every page to one tenant", async () => {
    signInAs("admin@veyra");
    renderShell("/sources");
    await screen.findByText("Acme NGFW (DMZ)");
    await userEvent.selectOptions(await screen.findByLabelText("Tenant"), "t_maha_power");
    expect(await screen.findByText("No sources yet. Onboard your first source.")).toBeInTheDocument();
  });

  it("shows a placeholder for pages other phases build, and sends unknown paths home", async () => {
    signInAs("admin@veyra");
    renderShell("/contracts/acme_ngfw_cef");
    await pageTitle("Contracts");
    expect(screen.getByText("This page isn't built yet.")).toBeInTheDocument();
    expect(document.querySelector('[data-phase="C6"]')).not.toBeNull();
  });

  it("redirects an unknown path to the Overview", async () => {
    signInAs("admin@veyra");
    renderShell("/no/such/page");
    await pageTitle("Overview");
  });

  it("signs out", async () => {
    signInAs("admin@veyra");
    renderShell();
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await pageTitle("Sign in to VEYRA");
  });
});
