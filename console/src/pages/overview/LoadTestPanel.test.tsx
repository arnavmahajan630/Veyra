import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import { LoadTestPanel } from "./LoadTestPanel";

const IDLE = { running: false, sent: 0, total: 0, outcome: null, error: null };

function renderPanel(...statuses: object[]) {
  // Each answer is served once, the last one from then on.
  let asked = 0;
  server.use(
    http.get("/api/control/load/status", () =>
      HttpResponse.json(statuses[Math.min(asked++, statuses.length - 1)]),
    ),
  );
  return renderWithProviders(<LoadTestPanel open onOpenChange={() => {}} />);
}

describe("LoadTestPanel", () => {
  it("sends the count as typed, after the field was emptied", async () => {
    let sent: unknown;
    server.use(
      http.post("/api/control/load/start", async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ status: "started" });
      }),
    );
    renderPanel(IDLE, { ...IDLE, running: true, total: 250000 });
    const count = await screen.findByLabelText("Total Events");
    const run = await screen.findByRole("button", { name: "Run Test" });

    await userEvent.clear(count);
    expect(count).toHaveValue(null);
    expect(run).toBeDisabled();
    await userEvent.type(count, "250000");
    expect(count).toHaveValue(250000);
    expect((count as HTMLInputElement).value).toBe("250000");

    await userEvent.click(run);
    expect(await screen.findByRole("button", { name: "Stop Test" })).toBeInTheDocument();
    expect(sent).toEqual({ count: 250000 });
    expect(screen.getByText("0 / 250,000")).toBeInTheDocument();
  });

  it("says why a start was refused, and shows the run it had lost track of", async () => {
    server.use(
      http.post("/api/control/load/start", () =>
        HttpResponse.json({ detail: "Load test already running" }, { status: 400 }),
      ),
    );
    renderPanel(IDLE, { ...IDLE, running: true, sent: 10, total: 1000 });
    await userEvent.click(await screen.findByRole("button", { name: "Run Test" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Load test already running");
    expect(await screen.findByRole("button", { name: "Stop Test" })).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("says that the last run failed, and why", async () => {
    renderPanel({
      ...IDLE,
      total: 1000,
      outcome: "failed",
      error: "Kafka is not answering; is the stack up and healthy? (exit code 1)",
    });
    expect(await screen.findByText("Failed")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Kafka is not answering");
    expect(screen.getByRole("button", { name: "Run Test" })).toBeEnabled();
  });

  it("reads a status without an outcome as idle (an older control-api)", async () => {
    renderPanel({ running: false, sent: 600, total: 600 });
    expect(await screen.findByText("Idle")).toBeInTheDocument();
    expect(screen.getByText("600 / 600")).toBeInTheDocument();
  });
});
