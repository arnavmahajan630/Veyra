import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { DriftItem } from "../../api/types";
import { DRIFT, SOURCES, healthAt } from "../../mocks/fixtures";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import SourcesPage from "./SourcesPage";

function renderPage(email = "admin@veyra", route = "/sources") {
  signInAs(email);
  return renderWithProviders(<SourcesPage />, { route });
}

const rowOf = (text: string) => screen.getByText(text).closest("tr") as HTMLElement;

describe("SourcesPage", () => {
  it("lists the registry joined with health", async () => {
    renderPage();
    expect(await screen.findByText("Acme NGFW (DMZ)")).toBeInTheDocument();
    expect(screen.getByText("Linux sshd (core)")).toBeInTheDocument();
    expect(await screen.findByText("acme_ngfw_cef@1")).toBeInTheDocument();
    expect(screen.getByText("+7%")).toBeInTheDocument();
    expect(screen.getByText("-812 ms")).toBeInTheDocument();
  });

  it("raises the silent-source alert on a quiet source", async () => {
    server.use(
      http.get("/api/lineage/sources", () =>
        HttpResponse.json(
          healthAt().map((h) =>
            h.source_id === "src_fw_dmz_01"
              ? { ...h, actual_eps: 0, last_seen: new Date(Date.now() - 300_000).toISOString() }
              : h,
          ),
        ),
      ),
    );
    renderPage();
    expect(await screen.findByText(/^No events for [45]m/)).toBeInTheDocument();
    expect(within(rowOf("Linux sshd (core)")).getByText("Active")).toBeInTheDocument();
  });

  it("alerts on a source that has never sent anything", async () => {
    const registered = new Date(Date.now() - 600_000).toISOString();
    server.use(
      http.get("/api/control/sources", () =>
        HttpResponse.json(SOURCES.map((s) => ({ ...s, created_at: registered }))),
      ),
      http.get("/api/lineage/sources", () =>
        HttpResponse.json(
          healthAt().map((h) =>
            h.source_id === "src_lnx_core_07" ? { ...h, actual_eps: 0, last_seen: null } : h,
          ),
        ),
      ),
    );
    renderPage();
    await screen.findByText(/^No events for (9|10)m/);
    const row = rowOf("Linux sshd (core)");
    expect(within(row).getByText("Never")).toBeInTheDocument();
    expect(within(row).getByText(/^No events for (9|10)m/)).toBeInTheDocument();
  });

  it("opens the drawer on a row and closes it", async () => {
    renderPage();
    await userEvent.click(await screen.findByText("Linux sshd (core)"));
    const drawer = await screen.findByRole("dialog", { name: "Linux sshd (core)" });
    expect(within(drawer).getByText("core-udp")).toBeInTheDocument();
    await userEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("issues a key, shows the secret once, then revokes it", async () => {
    renderPage("admin@veyra", "/sources?source=src_fw_dmz_01");
    const drawer = await screen.findByRole("dialog", { name: "Acme NGFW (DMZ)" });
    expect(await within(drawer).findByText("No keys issued for this source.")).toBeInTheDocument();

    await userEvent.click(within(drawer).getByRole("button", { name: "Issue a new key" }));
    expect(await within(drawer).findByText("Copy this secret now. It is shown only once.")).toBeInTheDocument();
    expect(within(drawer).getByText(/^veyra_mock/)).toBeInTheDocument();
    const keyRow = (await within(drawer).findByText("k_MOCK0001")).closest("tr") as HTMLElement;
    expect(within(keyRow).getByText("Active")).toBeInTheDocument();

    await userEvent.click(within(keyRow).getByRole("button", { name: "Revoke" }));
    const confirm = await screen.findByRole("dialog", { name: "Revoke key" });
    expect(confirm).toHaveTextContent("Revoke key k_MOCK0001?");
    await userEvent.click(within(confirm).getByRole("button", { name: "Revoke" }));
    expect(await within(keyRow).findByText("Revoked")).toBeInTheDocument();
  });

  it("hides key management from roles that cannot write", async () => {
    renderPage("approver@veyra", "/sources?source=src_fw_dmz_01");
    const drawer = await screen.findByRole("dialog", { name: "Acme NGFW (DMZ)" });
    await within(drawer).findByText("No keys issued for this source.");
    expect(within(drawer).queryByRole("button", { name: "Issue a new key" })).toBeNull();
  });

  it("lists the source's open unknown message shapes with their counts", async () => {
    const shape = { ...(DRIFT[0] as DriftItem), drift_id: "dr_fw_1", source_id: "src_fw_dmz_01", tenant_id: "t_ntro_core", state: "open" as const, count: 12, drain_template: "CEF:0|Acme|NGFW|<*>|<*>" };
    server.use(
      http.get("/api/control/drift", ({ request }) => {
        const url = new URL(request.url);
        const mine = url.searchParams.get("source_id") === "src_fw_dmz_01" && url.searchParams.get("state") === "open";
        return HttpResponse.json(mine ? [shape] : []);
      }),
    );
    renderPage();
    await userEvent.click(await screen.findByText("Acme NGFW (DMZ)"));
    const drawer = await screen.findByRole("dialog", { name: "Acme NGFW (DMZ)" });
    const link = await within(drawer).findByRole("link", { name: "CEF:0|Acme|NGFW|<*>|<*>" });
    expect(link).toHaveAttribute("href", "/drift/dr_fw_1");
    expect(within(link.closest("li") as HTMLElement).getByText("12")).toBeInTheDocument();
  });
});
