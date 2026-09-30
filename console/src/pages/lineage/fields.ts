// The normalized field list behind the Lineage event detail.
//
// This is the client half of P4: every row either points at bytes in the raw
// (`ulpf.field_offsets`) or declares itself computed (`ulpf.derived_fields`). A row in
// neither is an unexplained claim and is shown as such, never with a tick. The rules
// mirror `veyra_engine.engine.provenance_check` so the console agrees with the engine
// about what "verified" means.

import type { EventRevision } from "../../api/types";
import { byteSpanToChar } from "../../components/byteSpan";

/** Keys the engine sets as scaffolding rather than as claims about the source. */
const SCAFFOLDING = new Set([
  "class_uid",
  "category_uid",
  "type_uid",
  "activity_id",
  "time",
  "raw_data",
  "metadata",
  "observables",
  "unmapped",
  "enrichments",
  "ulpf",
]);

/** Rows worth showing first: the fields an analyst reads before anything else. */
const IMPORTANCE = [
  "user.name",
  "user.uid",
  "src_endpoint.ip",
  "src_endpoint.port",
  "dst_endpoint.ip",
  "dst_endpoint.port",
  "status_id",
  "status",
  "activity_name",
  "message",
];

export type Provenance = "extracted" | "constant" | "vocabulary" | "timestamp" | "derived" | "unexplained";

export interface NormalizedField {
  /** Dotted OCSF path; also the id shared with the raw pane's highlight span. */
  path: string;
  value: string;
  provenance: Provenance;
  /** [start, end) byte offsets into the decoded raw bytes; absent for computed fields. */
  byteSpan?: [number, number];
  /**
   * True when the span slices exactly this value out of the raw text. `null` for a field
   * that makes no byte claim (a constant, a vocabulary lookup) — there is nothing to check.
   */
  verified: boolean | null;
  /** Why the check failed, for the row's tooltip. */
  reason?: string;
}

/** Every leaf the OCSF body asserts about the source, as dotted paths. */
export function claimedPaths(ocsf: Record<string, unknown>, prefix = ""): string[] {
  const paths: string[] = [];
  for (const [key, value] of Object.entries(ocsf)) {
    if (!prefix && SCAFFOLDING.has(key)) {
      if (key === "observables" && Array.isArray(value)) {
        for (const observable of value) {
          const name = (observable as Record<string, unknown> | null)?.name;
          if (name) paths.push(`observables.${String(name)}`);
        }
      }
      continue;
    }
    const path = `${prefix}${key}`;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      paths.push(...claimedPaths(value as Record<string, unknown>, `${path}.`));
    } else if (Array.isArray(value)) {
      continue;
    } else if (value !== null && value !== undefined) {
      paths.push(path);
    }
  }
  return paths;
}

/** Read one dotted path out of the OCSF body (observables are named, not indexed). */
export function dig(ocsf: Record<string, unknown>, path: string): unknown {
  if (path.startsWith("observables.")) {
    const wanted = path.slice("observables.".length);
    const list = ocsf.observables;
    if (!Array.isArray(list)) return undefined;
    const hit = list.find((o) => (o as Record<string, unknown>)?.name === wanted);
    return (hit as Record<string, unknown> | undefined)?.value;
  }
  let cursor: unknown = ocsf;
  for (const part of path.split(".")) {
    if (!cursor || typeof cursor !== "object") return undefined;
    cursor = (cursor as Record<string, unknown>)[part];
  }
  return cursor;
}

function provenanceOf(declared: string | undefined, located: boolean): Provenance {
  if (declared) {
    if (declared === "const") return "constant";
    if (declared.startsWith("vocab:")) return "vocabulary";
    if (declared.startsWith("ts:")) return "timestamp";
    return "derived"; // "enrich", "base64", anything else the engine declares
  }
  return located ? "extracted" : "unexplained";
}

/**
 * Build the field rows for one revision, checking each located field against the raw text.
 *
 * `rawText` may be missing (the vault could not be read); located fields then keep their
 * span for the highlighter but report `verified: null` rather than claiming a pass.
 */
export function buildFields(revision: EventRevision, rawText?: string | null): NormalizedField[] {
  const ocsf = (revision.ocsf ?? {}) as Record<string, unknown>;
  const offsets = revision.field_offsets ?? {};
  const derived = revision.derived_fields ?? {};

  // Every claimed path, plus any offset for a path the body no longer carries — an offset
  // without a value is itself a provenance failure worth showing.
  const paths = Array.from(new Set([...claimedPaths(ocsf), ...Object.keys(offsets)]));

  const rows = paths.map((path) => {
    const raw = dig(ocsf, path);
    const value = raw === undefined ? "" : String(raw);
    const span = offsets[path];
    const provenance = provenanceOf(derived[path], span !== undefined);

    if (!span) {
      return { path, value, provenance, verified: provenance === "unexplained" ? false : null,
        reason: provenance === "unexplained"
          ? "neither located (field_offsets) nor declared computed (derived_fields)"
          : undefined } satisfies NormalizedField;
    }
    if (rawText === undefined || rawText === null) {
      return { path, value, provenance, byteSpan: span, verified: null } satisfies NormalizedField;
    }
    const [start, end] = byteSpanToChar(rawText, span);
    const sliced = rawText.slice(start, end);
    const ok = raw !== undefined && sliced === value;
    return {
      path,
      value,
      provenance,
      byteSpan: span,
      verified: ok,
      reason: ok ? undefined : `raw[${span[0]}:${span[1]}]=${JSON.stringify(sliced)} != ${JSON.stringify(value)}`,
    } satisfies NormalizedField;
  });

  const rank = (path: string) => {
    const index = IMPORTANCE.indexOf(path);
    return index === -1 ? IMPORTANCE.length : index;
  };
  return rows.sort((a, b) => rank(a.path) - rank(b.path) || a.path.localeCompare(b.path));
}

/** The verbatim parsed data the engine chose not to map. */
export function unmappedEntries(revision: EventRevision): [string, unknown][] {
  const unmapped = (revision.ocsf ?? {})["unmapped"];
  if (!unmapped || typeof unmapped !== "object") return [];
  return Object.entries(unmapped as Record<string, unknown>);
}

/** Tier-3 observables, which carry their own offsets under `observables.<name>`. */
export function observableEntries(revision: EventRevision): { name: string; value: string; type?: string }[] {
  const list = (revision.ocsf ?? {})["observables"];
  if (!Array.isArray(list)) return [];
  return list
    .filter((o): o is Record<string, unknown> => !!o && typeof o === "object")
    .map((o) => ({
      name: String(o.name ?? ""),
      value: String(o.value ?? ""),
      type: o.type === undefined ? undefined : String(o.type),
    }));
}
