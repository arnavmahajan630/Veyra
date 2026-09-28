import { describe, expect, it } from "vitest";
import type { Overview, SourceHealth } from "../api/types";
import { signInAs } from "./handlers";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  expect(response.status).toBe(200);
  return (await response.json()) as T;
}

describe("the mock world's tenant scope", () => {
  it("pins a tenant user to their tenant, whatever ?tenant= asks for", async () => {
    signInAs("author@maha");
    const overview = await get<Overview>("/api/lineage/overview?tenant=t_ntro_core");
    const health = await get<SourceHealth[]>("/api/lineage/sources?tenant=t_ntro_core");
    expect(overview.sources).toEqual([]);
    expect(overview.totals_by_tier).toEqual({ "1": 0, "2": 0, "3": 0, "4": 0 });
    expect(health).toEqual([]);
  });

  it("lets a platform user see every tenant or narrow to one", async () => {
    signInAs("admin@veyra");
    const all = await get<Overview>("/api/lineage/overview");
    const maha = await get<Overview>("/api/lineage/overview?tenant=t_maha_power");
    expect(all.sources.map((s) => s.source_id)).toEqual(["src_fw_dmz_01", "src_lnx_core_07"]);
    expect(maha.sources).toEqual([]);
  });

  it("sends 15 minutes of tier history with the HTTP overview", async () => {
    signInAs("admin@veyra");
    const overview = await get<Overview>("/api/lineage/overview");
    expect(overview.tier_history).toHaveLength(900);
  });
});

describe("the mock session", () => {
  it("survives a reload, as a session cookie would, and ends at sign-out", async () => {
    await fetch("/api/control/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: "approver@veyra", password: "veyra-demo" }),
    });
    expect(sessionStorage.getItem("veyra.mock.session")).toBe("approver@veyra");
    await fetch("/api/control/auth/logout", { method: "POST" });
    expect(sessionStorage.getItem("veyra.mock.session")).toBeNull();
  });
});
