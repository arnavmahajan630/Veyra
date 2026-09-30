import { describe, expect, it } from "vitest";
import type { EventRevision } from "../../api/types";
import { AUTH_EVENT, AUTH_RAW, OT_EVENT, OT_RAW, spanOf } from "../../mocks/lineageFixtures";
import { buildFields, claimedPaths, dig } from "./fields";

function revisionOf(event: typeof AUTH_EVENT, revision: number): EventRevision {
  const found = event.revisions.find((r) => r.revision === revision);
  if (!found) throw new Error(`no revision ${revision} in the fixture`);
  return found;
}

describe("claimedPaths", () => {
  it("skips scaffolding and names observables", () => {
    const paths = claimedPaths({
      class_uid: 3002,
      category_uid: 3,
      time: 1790000060,
      raw_data: "…",
      user: { name: "a.sharma" },
      src_endpoint: { ip: "103.21.4.77" },
      observables: [{ name: "ip_1", value: "103.21.4.77" }],
      unmapped: { junk: "ignored" },
    });
    expect(paths).toEqual(
      expect.arrayContaining(["user.name", "src_endpoint.ip", "observables.ip_1"]),
    );
    // Scaffolding and the bulky verbatim block are not claims about the source.
    for (const excluded of ["class_uid", "category_uid", "time", "raw_data", "unmapped.junk"]) {
      expect(paths).not.toContain(excluded);
    }
  });
});

describe("dig", () => {
  it("reads nested paths and named observables", () => {
    const ocsf = {
      user: { name: "a.sharma" },
      observables: [{ name: "ip_1", value: "103.21.4.77" }],
    };
    expect(dig(ocsf, "user.name")).toBe("a.sharma");
    expect(dig(ocsf, "observables.ip_1")).toBe("103.21.4.77");
    expect(dig(ocsf, "user.missing")).toBeUndefined();
  });
});

describe("buildFields", () => {
  it("verifies a located field against the exact raw bytes", () => {
    const fields = buildFields(revisionOf(AUTH_EVENT, 2), AUTH_RAW);
    const user = fields.find((f) => f.path === "user.name");
    expect(user).toMatchObject({ value: "a.sharma", provenance: "extracted", verified: true });
    expect(user?.byteSpan).toEqual(spanOf(AUTH_RAW, "a.sharma"));
  });

  it("labels a constant and a vocabulary lookup, and claims no byte span for them", () => {
    const fields = buildFields(revisionOf(AUTH_EVENT, 2), AUTH_RAW);
    const status = fields.find((f) => f.path === "status_id");
    expect(status).toMatchObject({ provenance: "constant", verified: null });
    expect(status?.byteSpan).toBeUndefined();
    expect(fields.find((f) => f.path === "activity_name")).toMatchObject({
      provenance: "vocabulary",
      verified: null,
    });
  });

  it("fails a field whose span does not slice out its value", () => {
    const revision = {
      ...revisionOf(AUTH_EVENT, 2),
      // One byte short: the classic off-by-one that a hardcoded span hides.
      field_offsets: { "user.name": [44, 51] as [number, number] },
      derived_fields: {},
      ocsf: { user: { name: "a.sharma" } },
    };
    const user = buildFields(revision, AUTH_RAW).find((f) => f.path === "user.name");
    expect(user?.verified).toBe(false);
    expect(user?.reason).toMatch(/!=/);
  });

  it("marks a claim that is neither located nor declared computed", () => {
    const revision = {
      ...revisionOf(AUTH_EVENT, 2),
      field_offsets: {},
      derived_fields: {},
      ocsf: { user: { name: "invented" } },
    };
    const user = buildFields(revision, AUTH_RAW).find((f) => f.path === "user.name");
    expect(user).toMatchObject({ provenance: "unexplained", verified: false });
  });

  it("reports verified: null when the raw bytes are unavailable", () => {
    const fields = buildFields(revisionOf(AUTH_EVENT, 2), null);
    for (const field of fields.filter((f) => f.byteSpan)) {
      expect(field.verified).toBeNull();
    }
  });

  it("handles multi-byte values: a Devanagari span still slices exactly", () => {
    const fields = buildFields(revisionOf(OT_EVENT, 1), OT_RAW);
    const user = fields.find((f) => f.path === "observables.user_1");
    expect(user).toMatchObject({ value: "r.deshmukh", verified: true });
    // The Devanagari offset is past multi-byte characters, so a byte span read as a char
    // span would slice the wrong text — this is the case AC1 names.
    const span = spanOf(OT_RAW, "चेतावनी");
    expect(span[1] - span[0]).toBeGreaterThan("चेतावनी".length);
  });

  it("puts the fields an analyst reads first at the top", () => {
    const paths = buildFields(revisionOf(AUTH_EVENT, 2), AUTH_RAW).map((f) => f.path);
    expect(paths.indexOf("user.name")).toBeLessThan(paths.indexOf("src_endpoint.ip"));
    expect(paths.indexOf("src_endpoint.ip")).toBeLessThan(paths.indexOf("status_id"));
  });
});
