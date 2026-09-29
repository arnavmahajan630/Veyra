import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { DRAFT_T3 } from "../../mocks/fixtures";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import { DraftReview } from "./DraftReview";

describe("DraftReview", () => {
  it("shows the class, the mappings with their bytes, the badge and the backtest", () => {
    signInAs("author@maha");
    renderWithProviders(<DraftReview draft={DRAFT_T3} mode="full" onSubmit={vi.fn()} />);
    expect(screen.getByText("Authentication / Logon")).toBeInTheDocument();
    const row = screen.getByRole("row", { name: /user\.name/ });
    expect(within(row).getByText("a.sharma")).toBeInTheDocument();
    expect(within(row).getByLabelText("Provenance checked")).toBeInTheDocument();
    expect(screen.getByText(/cache:qwen2\.5:3b/)).toBeInTheDocument();
    expect(screen.getByText(/Tier 4 → tier 1: 8/)).toBeInTheDocument();
  });

  it("hovering a row highlights its span in the raw bytes", async () => {
    signInAs("author@maha");
    renderWithProviders(<DraftReview draft={DRAFT_T3} mode="full" onSubmit={vi.fn()} />);
    await userEvent.hover(screen.getByRole("row", { name: /src_endpoint\.ip/ }));
    expect(screen.getByText("103.21.4.77", { selector: "mark" })).toHaveAttribute("data-tone", "active");
  });

  it("an edit that breaks provenance shows ✗ at once and disables Submit (AC4)", async () => {
    signInAs("author@maha");
    const broken = {
      ...DRAFT_T3,
      verification: {
        ...DRAFT_T3.verification!,
        ok: false,
        type_issues: ["src_endpoint.ip = 'a.sharma' is not an IP address"],
      },
    };
    server.use(http.patch("/api/control/drafts/dr_t3", () => HttpResponse.json(broken)));
    renderWithProviders(<DraftReview draft={DRAFT_T3} mode="full" onSubmit={vi.fn()} />);
    const row = screen.getByRole("row", { name: /src_endpoint\.ip/ });
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "a.sharma" }));
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByText("src_endpoint.ip = 'a.sharma' is not an IP address")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit for approval" })).toBeDisabled();
  });

  it("the field picker offers only fields no other row maps, so an edit can't map one twice", async () => {
    signInAs("author@maha");
    renderWithProviders(<DraftReview draft={DRAFT_T3} mode="full" onSubmit={vi.fn()} />);
    const row = screen.getByRole("row", { name: /src_endpoint\.ip/ });
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const options = within(screen.getByRole("combobox", { name: "OCSF field" })).getAllByRole("option");
    const fields = options.map((o) => o.textContent);
    expect(fields).toContain("src_endpoint.ip");
    expect(fields).not.toContain("user.name");
    expect(fields).not.toContain("dst_endpoint.ip");
  });

  it("an edit the backend refuses shows its reason", async () => {
    signInAs("author@maha");
    server.use(
      http.patch("/api/control/drafts/dr_t3", () =>
        HttpResponse.json({ detail: "src_endpoint.ip: const 7 is not an allowed enum value" }, { status: 422 }),
      ),
    );
    renderWithProviders(<DraftReview draft={DRAFT_T3} mode="full" onSubmit={vi.fn()} />);
    const row = screen.getByRole("row", { name: /src_endpoint\.ip/ });
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("const 7 is not an allowed enum value");
  });

  it("compact mode shows only the class line, the table and the badge", () => {
    signInAs("author@maha");
    renderWithProviders(<DraftReview draft={DRAFT_T3} mode="compact" />);
    expect(screen.queryByText(/Tier 4 → tier 1/)).not.toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });
});
