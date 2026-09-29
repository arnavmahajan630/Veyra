import { describe, expect, it } from "vitest";
import { DRAFT_T3 } from "../../mocks/fixtures";
import { buildRows, firstFailure, rowsToEdit } from "./mappings";

const template = DRAFT_T3.templates[0]!;

describe("mapping rows", () => {
  it("joins the response, the spans and the provenance", () => {
    const rows = buildRows(template, DRAFT_T3.verification);
    const user = rows.find((r) => r.ocsf_path === "user.name")!;
    expect(user).toMatchObject({ value: "a.sharma", spanId: "k2", state: "ok" });
    const status = rows.find((r) => r.ocsf_path === "status_id")!;
    expect(status).toMatchObject({ value: "2 Failure", state: "derived" });
  });

  it("marks a heuristic disagreement for review and a failed check as failed", () => {
    const rows = buildRows(
      { ...template, review: ["src_endpoint.ip"] },
      { ...DRAFT_T3.verification!, provenance: [{ ocsf_path: "user.name", ok: false, reason: "raw[1:2]='x'", kind: "located" }] },
    );
    expect(rows.find((r) => r.ocsf_path === "src_endpoint.ip")?.state).toBe("review");
    expect(rows.find((r) => r.ocsf_path === "user.name")).toMatchObject({ state: "failed", reason: "raw[1:2]='x'" });
  });

  it("round-trips rows to an edit and finds the first failure", () => {
    expect(rowsToEdit(buildRows(template, DRAFT_T3.verification))).toEqual(template.response.mappings);
    expect(firstFailure(DRAFT_T3.verification)).toBeNull();
    expect(firstFailure({ ...DRAFT_T3.verification!, type_issues: ["src_endpoint.ip = 'bob' is not an IP address"] }))
      .toBe("src_endpoint.ip = 'bob' is not an IP address");
  });
});
