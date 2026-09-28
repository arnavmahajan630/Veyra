import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { signInAs } from "../mocks/handlers";
import { makeQueryClient, Providers } from "../test/render";
import { useIssueKey, useLogin, useMe, useRevokeKey, useSourceKeys, useSources } from "./queries";

function wrapper() {
  const client = makeQueryClient();
  return ({ children }: { children: ReactNode }) => <Providers client={client}>{children}</Providers>;
}

describe("auth hooks", () => {
  it("signs in and exposes the user", async () => {
    const { result } = renderHook(() => ({ me: useMe(), login: useLogin() }), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.me.isError).toBe(true));
    await act(() => result.current.login.mutateAsync({ email: "author@maha", password: "veyra-demo" }));
    await waitFor(() => expect(result.current.me.data?.role).toBe("pack_author"));
  });

  it("rejects a wrong password", async () => {
    const { result } = renderHook(() => useLogin(), { wrapper: wrapper() });
    await expect(result.current.mutateAsync({ email: "author@maha", password: "nope" })).rejects.toMatchObject({
      status: 401,
    });
  });
});

describe("data hooks", () => {
  it("filters sources by tenant", async () => {
    signInAs("approver@veyra");
    const { result } = renderHook(() => useSources("t_maha_power"), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.data).toEqual([]));
  });

  it("issues a key, lists it, then revokes it", async () => {
    signInAs("admin@veyra");
    const { result } = renderHook(
      () => ({
        keys: useSourceKeys("src_fw_dmz_01"),
        issue: useIssueKey("src_fw_dmz_01"),
        revoke: useRevokeKey("src_fw_dmz_01"),
      }),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.keys.data).toEqual([]));
    const card = await act(() => result.current.issue.mutateAsync());
    expect(card.secret).toMatch(/^veyra_/);
    await waitFor(() => expect(result.current.keys.data?.[0]?.status).toBe("active"));
    await act(() => result.current.revoke.mutateAsync(card.key_id));
    await waitFor(() => expect(result.current.keys.data?.[0]?.status).toBe("revoked"));
  });
});
