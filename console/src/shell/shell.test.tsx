import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { resetMockState, signInAs } from "../mocks/handlers";
import { renderWithProviders } from "../test/render";
import { server } from "../test/server";
import { AppShell } from "./AppShell";
import { createQueryClient } from "./queryClient";

function renderShell(route = "/") {
  return renderWithProviders(<AppShell />, { route, client: createQueryClient() });
}

const pageTitle = (name: string | RegExp) => screen.findByRole("heading", { level: 1, name });

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
    expect(await screen.findByText("Maha Power auth server")).toBeInTheDocument();
    expect(screen.queryByText("Acme NGFW (DMZ)")).not.toBeInTheDocument();
  });

  it("routes Track B pages to real pages, not placeholders", async () => {
    signInAs("admin@veyra");
    const pages: [string, string | RegExp][] = [
      ["/lineage", "Lineage Explorer"],
      ["/evidence", "Evidence & Integrity"],
      ["/demo", "Demo Control Engine"],
    ];
    for (const [route, title] of pages) {
      const view = renderShell(route);
      await pageTitle(title);
      expect(document.querySelector("[data-phase]")).toBeNull();
      view.unmount();
    }
  });

  it("does not serve /demo outside demo mode, even by typing the URL", async () => {
    // The nav hides the item; the router must refuse the route too, or the demo panel is
    // reachable on a non-demo deployment (B7).
    signInAs("admin@veyra");
    server.use(
      http.get("/api/control/auth/me", () =>
        HttpResponse.json({
          user: { email: "admin@veyra", name: "Admin" },
          role: "admin",
          tenant: "*",
          demo_mode: false,
        }),
      ),
    );
    renderShell("/demo");
    // Unknown paths redirect to the Overview.
    await pageTitle("Overview");
    expect(screen.queryByText("Demo Control Engine")).not.toBeInTheDocument();
  });

  it("routes every C6 page to the real page, not a placeholder", async () => {
    signInAs("admin@veyra");
    const pages: [string, string | RegExp][] = [
      ["/onboard", "Onboard a source"],
      ["/contracts", "Contracts"],
      ["/contracts/acme_ngfw_cef", "acme_ngfw_cef"],
      ["/drift", "Unknown message shapes"],
      ["/drift/dr_item_t3", /FAILED login/],
      ["/delivery", "Delivery"],
      ["/audit", "Audit"],
    ];
    for (const [route, title] of pages) {
      const view = renderShell(route);
      await pageTitle(title);
      expect(document.querySelector("[data-phase]")).toBeNull();
      view.unmount();
    }
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
