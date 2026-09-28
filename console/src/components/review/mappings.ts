import type { DraftMapping, DraftTemplate, Verification } from "../../api/types";

export type RowState = "ok" | "failed" | "review" | "derived";

export interface Row {
  ocsf_path: string;
  token?: string;
  const?: number;
  value: string;
  spanId?: string;
  state: RowState;
  reason: string;
}

export function buildRows(template: DraftTemplate, verification: Verification | null): Row[] {
  const spans = new Map(template.spans.map((s) => [s.id, s]));
  const checks = new Map((verification?.provenance ?? []).map((p) => [p.ocsf_path, p]));
  return template.response.mappings.map((m) => {
    const check = checks.get(m.ocsf_path);
    if (m.const !== undefined) {
      const label = template.request.enums[m.ocsf_path]?.[String(m.const)];
      return { ocsf_path: m.ocsf_path, const: m.const, value: label ? `${m.const} ${label}` : String(m.const),
               state: "derived", reason: "" };
    }
    const span = m.token ? spans.get(m.token) : undefined;
    let state: RowState = "ok";
    if (check && !check.ok) state = "failed";
    else if (template.review.includes(m.ocsf_path)) state = "review";
    return { ocsf_path: m.ocsf_path, token: m.token, value: span?.value ?? m.token ?? "", spanId: span?.id,
             state, reason: check && !check.ok ? check.reason : "" };
  });
}

export function rowsToEdit(rows: Row[]): DraftMapping[] {
  return rows.map((r) => (r.const !== undefined ? { ocsf_path: r.ocsf_path, const: r.const } : { ocsf_path: r.ocsf_path, token: r.token }));
}

export function firstFailure(verification: Verification | null): string | null {
  if (!verification) return null;
  if (verification.compile_error) return verification.compile_error.message;
  const row = verification.provenance.find((p) => !p.ok);
  return row ? `${row.ocsf_path}: ${row.reason}` : verification.type_issues[0] ?? null;
}
