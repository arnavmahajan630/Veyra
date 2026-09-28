import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { signInAs } from "../mocks/handlers";
import { makeQueryClient, Providers } from "../test/render";
import { useContracts, useDraft, useDrift } from "./queries";

let client = makeQueryClient();
beforeEach(() => {
  client = makeQueryClient();
});
const wrapper = ({ children }: { children: ReactNode }) => <Providers client={client}>{children}</Providers>;

describe("C6 hooks against the mock world", () => {
  it("lists the author's contracts only", async () => {
    signInAs("author@maha");
    const { result } = renderHook(() => useContracts(null), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.map((c) => c.id)).toEqual(["authsrv"]);
  });

  it("reads the drift inbox and a ready draft", async () => {
    signInAs("author@maha");
    // control-api filters on the exact state; the T3 item is already draft_ready (auto-draft).
    const drift = renderHook(() => useDrift(null), { wrapper });
    await waitFor(() => expect(drift.result.current.data?.length).toBe(1));
    const draft = renderHook(() => useDraft("dr_t3"), { wrapper });
    await waitFor(() => expect(draft.result.current.data?.state).toBe("ready"));
    expect(draft.result.current.data?.templates[0]?.spans.length).toBe(4);
  });

  it("filters the drift inbox by state and source", async () => {
    signInAs("author@maha");
    const ready = renderHook(() => useDrift("draft_ready", "src_authsrv_01"), { wrapper });
    await waitFor(() => expect(ready.result.current.data?.map((d) => d.drift_id)).toEqual(["dr_item_t3"]));
    const other = renderHook(() => useDrift("open", "src_authsrv_01"), { wrapper });
    await waitFor(() => expect(other.result.current.data).toEqual([]));
  });
});
