import { describe, expect, it } from "vitest";
import { api, ApiError, CONTROL } from "../api/client";
import { postStream, type SseFrame } from "../api/stream";
import type { Draft, DriftItem, ReplayJob, VersionDetail } from "../api/types";
import { signInAs } from "./handlers";

async function refusal(promise: Promise<unknown>): Promise<ApiError> {
  const error = await promise.then(
    () => null,
    (e: unknown) => e,
  );
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}

describe("the C6 mock world", () => {
  it("refuses the author on role, and an approver approving their own draft on four-eyes", async () => {
    signInAs("author@maha");
    const v2 = await api.post<VersionDetail>(`${CONTROL}/drafts/dr_t3/submit`);
    expect([v2.contract_id, v2.version, v2.state]).toEqual(["authsrv", 2, "canary"]);
    const role = await refusal(api.post(`${CONTROL}/contracts/authsrv/versions/2/approve`));
    expect([role.status, role.message]).toEqual([403, "this action needs one of the roles: admin, pack_approver"]);

    signInAs("admin@veyra");
    const own = await api.post<VersionDetail>(`${CONTROL}/drafts/dr_onboard/submit`);
    const fourEyes = await refusal(api.post(`${CONTROL}/contracts/${own.contract_id}/versions/1/approve`));
    expect(fourEyes.message).toBe("four-eyes: admin@veyra submitted auth_server@1, so someone else must approve it");
  });

  it("resolves the drift item once the version its draft became is promoted", async () => {
    signInAs("author@maha");
    await api.post(`${CONTROL}/drafts/dr_t3/submit`);
    signInAs("approver@veyra");
    await api.post(`${CONTROL}/contracts/authsrv/versions/2/approve`);
    const promoted = await api.post<VersionDetail>(`${CONTROL}/contracts/authsrv/versions/2/promote`);
    expect(promoted.state).toBe("active");
    const item = await api.get<DriftItem>(`${CONTROL}/drift/dr_item_t3`);
    expect([item.state, item.resolved_by]).toEqual(["resolved", "authsrv@2"]);
  });

  it("finishes a replay by polling alone", async () => {
    signInAs("approver@veyra");
    const job = await api.post<ReplayJob>(`${CONTROL}/replay`, { contract_id: "authsrv", template_sigs: [] });
    const seen: string[] = [];
    for (let i = 0; i < 4; i += 1) {
      const next = await api.get<ReplayJob>(`${CONTROL}/replay/${job.job_id}`);
      seen.push(`${next.state}:${next.normalized}`);
    }
    expect(seen).toEqual(["normalizing:3", "normalizing:6", "done:8", "done:8"]);
  });

  it("re-verifies an edit: a token that isn't in the sample fails provenance", async () => {
    signInAs("author@maha");
    const draft = await api.patch<Draft>(`${CONTROL}/drafts/dr_t3`, {
      mappings: [{ ocsf_path: "user.name", token: "k99" }],
    });
    expect(draft.verification?.ok).toBe(false);
    expect(draft.verification?.provenance.find((r) => r.ocsf_path === "user.name")?.ok).toBe(false);
  });

  it("flags an IP field mapped to a non-IP token as a type issue", async () => {
    signInAs("author@maha");
    const draft = await api.patch<Draft>(`${CONTROL}/drafts/dr_t3`, {
      mappings: [{ ocsf_path: "src_endpoint.ip", token: "k2" }],
    });
    expect(draft.verification?.type_issues).toEqual(["src_endpoint.ip: a.sharma is not an IP address"]);
  });

  it("streams the onboarding analysis through postStream, frame by frame", async () => {
    signInAs("author@maha");
    await api.post(`${CONTROL}/sources`, { id: "src_auth_server_01", tenant_id: "t_maha_power", name: "Auth Server" });
    const frames: SseFrame[] = [];
    await postStream(`${CONTROL}/onboarding/analyze`, { source_id: "src_auth_server_01", samples: ["x"] }, (f) => frames.push(f));
    expect(frames.map((f) => f.event)).toEqual(["classification", "templates", "library", "draft", "draft", "done"]);
    expect(frames.at(-1)?.data).toMatchObject({ draft_id: "dr_onboard" });
  });

  it("refuses a second source with the same id", async () => {
    signInAs("author@maha");
    const again = await refusal(
      api.post(`${CONTROL}/sources`, { id: "src_authsrv_01", tenant_id: "t_maha_power", name: "dup" }),
    );
    expect(again.status).toBe(409);
  });
});
