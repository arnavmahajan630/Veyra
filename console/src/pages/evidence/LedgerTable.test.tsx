import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { LedgerRoot } from "../../api/types";
import { ROOTS, rootFixture } from "../../mocks/lineageFixtures";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import { LedgerTable } from "./LedgerTable";

/** A fake EventSource so a jsdom test can push SSE events on demand. */
class FakeSource {
  static last: FakeSource | null = null;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  private listeners = new Map<string, (event: MessageEvent<string>) => void>();

  constructor(readonly url: string) {
    FakeSource.last = this;
  }
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.set(type, listener);
  }
  emit(type: string, data: unknown) {
    this.listeners.get(type)?.(new MessageEvent(type, { data: JSON.stringify(data) }));
  }
  close() {}
}

function rootsResponse(roots: LedgerRoot[], auditStatus = "PASS") {
  return HttpResponse.json({
    count: roots.length,
    ledger: "data/vault/roots/ledger.ndjson",
    audit_status: auditStatus,
    roots,
  });
}

describe("LedgerTable", () => {
  it("seeds from /evidence/roots", async () => {
    signInAs("admin@veyra");
    renderWithProviders(<LedgerTable />);
    expect(await screen.findByText("w_1790000060")).toBeInTheDocument();
    expect(screen.getByText("w_1790000000")).toBeInTheDocument();
  });

  it("appends a newly signed root from the SSE stream", async () => {
    signInAs("admin@veyra");
    const sources: FakeSource[] = [];
    renderWithProviders(
      <LedgerTable
        createSource={(url) => {
          const source = new FakeSource(url);
          sources.push(source);
          return source;
        }}
      />,
    );
    await screen.findByText("w_1790000060");

    // The evidence API announces each seal as `event: root`; the table must not need a
    // refetch to show it.
    const fresh = rootFixture("w_1790000120", 1790000120, 5, "c".repeat(64));
    // The component builds its own EventSource, so drive the one it made.
    expect(sources, "the table should have opened the lineage stream").toHaveLength(1);
    sources[0]!.emit("root", fresh);
    await waitFor(() => expect(screen.getByText("w_1790000120")).toBeInTheDocument());
  });

  it("shows a broken prev link as broken, not as a tick", async () => {
    signInAs("admin@veyra");
    const broken = rootFixture("w_1790000060", 1790000060, 8, "b".repeat(64), {
      prev_link_ok: false,
      chain_ok: false,
    });
    server.use(http.get("/api/evidence/roots", () => rootsResponse([broken, ROOTS[1]!], "FAIL")));
    renderWithProviders(<LedgerTable />);
    const row = await screen.findByText("w_1790000060");
    const cells = row.closest("tr");
    expect(cells).toHaveTextContent("broken");
  });

  it("says unknown when the ledger audit could not run", async () => {
    signInAs("admin@veyra");
    const unaudited: LedgerRoot = {
      window_id: "w_1790000060",
      payload: { window_start: 1790000060, window_end: 1790000120, leaf_count: 3 },
      sig_b64: "x",
      payload_sha256: "d".repeat(64),
    };
    server.use(http.get("/api/evidence/roots", () => rootsResponse([unaudited], "unknown")));
    renderWithProviders(<LedgerTable />);
    const row = (await screen.findByText("w_1790000060")).closest("tr");
    expect(row).toHaveTextContent("unknown");
    expect(row).not.toHaveTextContent("Ed25519");
  });

  it("never claims immudb anchoring, which is a prototype in this build", async () => {
    signInAs("admin@veyra");
    renderWithProviders(<LedgerTable />);
    const row = (await screen.findByText("w_1790000060")).closest("tr");
    expect(row).toHaveTextContent("prototype");
  });
});
