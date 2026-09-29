import type { AuditRow } from "../../api/types";

const COLUMNS = ["actor", "role", "action", "target", "detail", "at"] as const;

/** RFC 4180: a field with a comma, quote or line break is quoted, and quotes are doubled. */
function field(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;
}

/** The audit rows as CSV, CRLF line endings, header first. */
export function auditCsv(rows: readonly AuditRow[]): string {
  const lines = [COLUMNS.join(","), ...rows.map((row) => COLUMNS.map((c) => field(row[c])).join(","))];
  return lines.map((line) => `${line}\r\n`).join("");
}

/** Hand the CSV to the browser as a download, e.g. `veyra-audit-2026-09-29.csv`. */
export function downloadCsv(text: string, name: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}
