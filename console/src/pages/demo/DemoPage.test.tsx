import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installHotkeyListener } from "../../hotkeys/registry";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import DemoPage from "./DemoPage";

let uninstall: (() => void) | undefined;
beforeEach(() => {
  // App.tsx installs the global key listener; a component test must do the same.
  uninstall = installHotkeyListener();
});
afterEach(() => uninstall?.());

function renderPanel(email = "admin@veyra") {
  signInAs(email);
  return renderWithProviders(<DemoPage />, { route: "/demo" });
}

function stageButton(n: number) {
  return document.querySelector(`[data-stage="${n}"]`) as HTMLElement | null;
}

describe("DemoPage", () => {
  it("reads the stage list from the scenario rather than hardcoding it", async () => {
    renderPanel();
    // These titles come from sih_main, not from the component.
    expect(await screen.findByText(/1\. Hook/)).toBeInTheDocument();
    expect(screen.getByText(/3\. Log storm/)).toBeInTheDocument();
    expect(screen.getByText(/4\. Drift loop/)).toBeInTheDocument();
  });

  it("marks a stage done only once the engine reports its expectations held", async () => {
    renderPanel();
    await screen.findByText(/3\. Log storm/);
    await userEvent.click(stageButton(3)!);
    await waitFor(() => expect(stageButton(3)).toHaveAttribute("data-state", "done"));
    // And the expectations themselves are shown, each with its own verdict.
    const expectRows = document.querySelectorAll("[data-expect]");
    expect(expectRows.length).toBe(2);
    for (const row of expectRows) expect(row.getAttribute("data-ok")).toBe("true");
  });

  it("shows a failed stage as failed with the engine's reason", async () => {
    renderPanel();
    await screen.findByText(/3\. Log storm/);
    server.use(
      http.get("/api/demo/stage/status", () =>
        HttpResponse.json({
          stage: 3,
          state: "failed",
          results: { "wazuh rule 100111 for 45.12.3.9": false },
          error: "NoLiveKey: no API key has been issued since the last reset",
        }),
      ),
    );
    await userEvent.click(stageButton(3)!);
    await waitFor(() => expect(stageButton(3)).toHaveAttribute("data-state", "failed"));
    expect(await screen.findByText(/no API key has been issued/)).toBeInTheDocument();
  });

  it("runs a stage from its hotkey, and a double press does not double-send", async () => {
    let posts = 0;
    server.use(
      http.post("/api/demo/stage/:id", ({ params }) => {
        posts += 1;
        return HttpResponse.json({ ok: true, stage: Number(params.id) });
      }),
    );
    renderPanel();
    await screen.findByText(/3\. Log storm/);
    await userEvent.keyboard("{Shift>}3{/Shift}");
    await userEvent.keyboard("{Shift>}3{/Shift}");
    await waitFor(() => expect(posts).toBeGreaterThan(0));
    // The second press lands while the first is pending, so it is dropped.
    expect(posts).toBe(1);
  });

  it("asks before resetting and then shows each step with its timing", { timeout: 20000 }, async () => {
    renderPanel();
    await userEvent.keyboard("{Shift>}R{/Shift}");
    expect(await screen.findByText(/Reset the whole demo\?/)).toBeInTheDocument();
    // The confirmation says the signing keys survive, which is the thing people worry about.
    expect(screen.getByText(/signing keys are kept/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Reset now" }));
    const verdict = await screen.findByTestId("reset-verdict", undefined, { timeout: 15000 });
    expect(verdict).toHaveTextContent(/Reset complete/);
    expect(document.querySelectorAll("[data-step]").length).toBeGreaterThan(0);
  });

  it("shows every preflight row with its measured status, warnings included", async () => {
    renderPanel();
    const ollama = await screen.findByText(/model resident on CPU/);
    expect(ollama).toBeInTheDocument();
    const row = ollama.closest("li");
    expect(within(row!).getByText("Ollama GPU")).toBeInTheDocument();
    expect(row?.querySelector('[data-status="WARN"]')).toBeTruthy();
    expect(screen.getByText(/pass ·/)).toBeInTheDocument();
  });

  it("offers every B5 mode plus Untamper against a chosen event", async () => {
    renderPanel();
    await waitFor(() =>
      expect(document.querySelectorAll("[data-mode]").length).toBe(4),
    );
    for (const mode of ["naive_flip", "insider_rewrite", "segment_delete", "root_rewrite"]) {
      expect(document.querySelector(`[data-mode="${mode}"]`)).toBeTruthy();
    }
    // The picker defaults to an event, so a tamper never posts without a target.
    const picker = screen.getByLabelText(/Target event/) as HTMLSelectElement;
    expect(picker.value).toMatch(/^0192a4f0-/);

    let sentUid: string | undefined;
    server.use(
      http.post("/api/demo/tamper", async ({ request }) => {
        const body = (await request.json()) as { event_uid?: string; mode?: string };
        sentUid = body.event_uid;
        return HttpResponse.json({ target: "seg", mode: body.mode, detail: "applied" });
      }),
    );
    await userEvent.click(document.querySelector('[data-mode="insider_rewrite"]') as HTMLElement);
    await waitFor(() => expect(sentUid).toBe(picker.value));
  });

  it("names the current beat and how far a rehearsal has drifted", async () => {
    renderPanel();
    expect(await screen.findByTestId("beat-elapsed")).toHaveTextContent("0:00");
    expect(screen.getByTestId("beat-current")).toHaveTextContent("Beat 1: Hook");
  });

  it("registers no demo hotkey when the session is not in demo mode", async () => {
    let posts = 0;
    server.use(
      http.post("/api/demo/stage/:id", () => {
        posts += 1;
        return HttpResponse.json({ ok: true, stage: 3 });
      }),
      http.get("/api/control/auth/me", () =>
        HttpResponse.json({
          user: { email: "admin@veyra", name: "Admin" },
          role: "admin",
          tenant: "*",
          demo_mode: false,
        }),
      ),
    );
    renderPanel();
    await screen.findByRole("heading", { level: 1 });
    await userEvent.keyboard("{Shift>}3{/Shift}");
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(posts).toBe(0);
  });
});
