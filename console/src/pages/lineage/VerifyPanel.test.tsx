import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { installHotkeyListener } from "../../hotkeys/registry";
import { AUTH_UID, OT_UID, verifyOk, verifyTampered } from "../../mocks/lineageFixtures";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import { VerifyPanel, failureSummary, isNeutral } from "./VerifyPanel";

function chip(id: string): HTMLElement | null {
  return document.querySelector(`[data-step="${id}"]`);
}

function states(): Record<string, string | null> {
  return Object.fromEntries(
    Array.from(document.querySelectorAll("[data-step]")).map((el) => [
      el.getAttribute("data-step"),
      el.getAttribute("data-state"),
    ]),
  );
}

async function verifyAll(uid = AUTH_UID, demoMode = true) {
  signInAs("admin@veyra");
  const view = renderWithProviders(<VerifyPanel eventUid={uid} demoMode={demoMode} />);
  await userEvent.click(screen.getByRole("button", { name: /verify evidence/i }));
  // Eight chips at 120 ms of stagger each.
  await waitFor(() => expect(Object.keys(states())).toHaveLength(8));
  await waitFor(() => expect(Object.values(states())).not.toContain("hidden"), { timeout: 3000 });
  return view;
}

// App.tsx installs the one global key listener; a component test has to do the same or
// no registered hotkey can ever fire.
let uninstall: (() => void) | undefined;
beforeEach(() => {
  uninstall = installHotkeyListener();
});
afterEach(() => {
  uninstall?.();
});

describe("failureSummary", () => {
  it("locates the tamper in time when the root still verifies", () => {
    const summary = failureSummary(verifyTampered(AUTH_UID).steps);
    expect(summary).toMatch(/altered after it was sealed/);
    expect(summary).toMatch(/signed root still verifies/);
  });

  it("is empty for an honest report", () => {
    expect(failureSummary(verifyOk(AUTH_UID).steps)).toBe("");
  });
});

describe("isNeutral", () => {
  it("treats a prototype step and a pending seal as neither pass nor failure", () => {
    const steps = verifyOk(AUTH_UID).steps;
    expect(isNeutral(steps[steps.length - 1]!)).toBe(true);
    expect(isNeutral(steps[0]!)).toBe(false);
  });
});

describe("VerifyPanel", () => {
  it("reveals nothing before the server answers", async () => {
    signInAs("admin@veyra");
    let release: (() => void) | undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.get("/api/evidence/:uid/verify", async () => {
        await held;
        return HttpResponse.json(verifyOk(AUTH_UID));
      }),
    );
    renderWithProviders(<VerifyPanel eventUid={AUTH_UID} />);
    await userEvent.click(screen.getByRole("button", { name: /verify evidence/i }));
    // The honesty rule: no chip exists at all until the report is in hand.
    expect(document.querySelectorAll("[data-step]")).toHaveLength(0);
    await act(async () => {
      release?.();
      await held;
    });
    await waitFor(() => expect(document.querySelectorAll("[data-step]")).toHaveLength(8));
  });

  it("shows seven green steps and immudb grey, and calls the event verified", async () => {
    await verifyAll();
    const seen = states();
    expect(seen["immudb_verified"]).toBe("neutral");
    const green = Object.entries(seen).filter(([, state]) => state === "ok");
    expect(green).toHaveLength(7);
    expect(Object.values(seen)).not.toContain("failed");
  });

  it("uses the translated chip labels, not the raw step ids", async () => {
    await verifyAll();
    expect(chip("merkle_inclusion")).toHaveTextContent("Merkle");
    expect(chip("merkle_inclusion")).not.toHaveTextContent("merkle_inclusion");
  });

  it("greys a pending seal instead of failing it", async () => {
    await verifyAll(OT_UID);
    const seen = states();
    expect(seen["merkle_inclusion"]).toBe("neutral");
    expect(seen["root_signature"]).toBe("neutral");
    expect(screen.queryByTestId("verify-failure-summary")).not.toBeInTheDocument();
  });

  it("turns red at the tampered steps and breaks the thread there", async () => {
    signInAs("admin@veyra");
    server.use(http.get("/api/evidence/:uid/verify", () => HttpResponse.json(verifyTampered(AUTH_UID))));
    await verifyAll();
    const seen = states();
    expect(seen["hash_raw"]).toBe("failed");
    expect(seen["merkle_inclusion"]).toBe("failed");
    // The root itself is still sound — that contrast is the point of the beat.
    expect(seen["root_signature"]).toBe("ok");
    const summary = await screen.findByTestId("verify-failure-summary");
    expect(summary).toHaveTextContent(/altered after it was sealed/);
    // The link breaks at the first failure and stays broken.
    const links = Array.from(document.querySelectorAll("[data-link]")).map((el) =>
      el.getAttribute("data-link"),
    );
    expect(links.filter((l) => l === "broken").length).toBeGreaterThan(0);
  });

  it("tampers and re-verifies on Shift+T in demo mode", async () => {
    await verifyAll();
    expect(states()["hash_raw"]).toBe("ok");
    await userEvent.keyboard("{Shift>}T{/Shift}");
    // The mock remembers the tamper, so the next verify answers red.
    await waitFor(() => expect(states()["hash_raw"]).toBe("failed"), { timeout: 4000 });
  });

  it("goes green again after Untamper", async () => {
    await verifyAll();
    await userEvent.keyboard("{Shift>}T{/Shift}");
    await waitFor(() => expect(states()["hash_raw"]).toBe("failed"), { timeout: 4000 });
    await userEvent.click(screen.getByRole("button", { name: /untamper/i }));
    await waitFor(() => expect(states()["hash_raw"]).toBe("ok"), { timeout: 4000 });
  });

  it("does not register Shift+T outside demo mode", async () => {
    await verifyAll(AUTH_UID, false);
    await userEvent.keyboard("{Shift>}T{/Shift}");
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(states()["hash_raw"]).toBe("ok");
    expect(screen.queryByRole("button", { name: /untamper/i })).not.toBeInTheDocument();
  });

  it("surfaces a failed verify request instead of showing a blank chain", async () => {
    signInAs("admin@veyra");
    server.use(
      http.get("/api/evidence/:uid/verify", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    renderWithProviders(<VerifyPanel eventUid={AUTH_UID} />);
    await userEvent.click(screen.getByRole("button", { name: /verify evidence/i }));
    expect(await screen.findByText(/verify request itself failed/i)).toBeInTheDocument();
  });
});
