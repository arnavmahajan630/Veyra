import { useState } from "react";
import { useAudit } from "../../api/queries";
import type { AuditRow } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { useI18n } from "../../i18n/i18n";
import { auditCsv, downloadCsv } from "./csv";

const matches = (row: AuditRow, text: string) =>
  [row.actor, row.action, row.target].some((value) => value.toLowerCase().includes(text));

/** The audit log (IF-AUDIT, scoped by control-api), filterable and exportable as CSV (C6). */
export default function AuditPage() {
  const { t } = useI18n();
  const audit = useAudit();
  const [filter, setFilter] = useState("");
  const text = filter.trim().toLowerCase();
  const rows = (audit.data ?? []).filter((row) => text === "" || matches(row, text));

  const columns: Column<AuditRow>[] = [
    { id: "at", header: t("audit.col.at"), cell: (r) => <span className="font-mono text-meta">{r.at.replace("T", " ").slice(0, 19)}</span>, sortValue: (r) => r.at },
    { id: "actor", header: t("audit.col.actor"), cell: (r) => r.actor, sortValue: (r) => r.actor },
    { id: "role", header: t("audit.col.role"), cell: (r) => r.role },
    { id: "action", header: t("audit.col.action"), cell: (r) => <code className="font-mono text-meta">{r.action}</code>, sortValue: (r) => r.action },
    { id: "target", header: t("audit.col.target"), cell: (r) => <code className="font-mono text-meta">{r.target}</code> },
    { id: "detail", header: t("audit.col.detail"), cell: (r) => r.detail },
  ];

  return (
    <div className="flex flex-col gap-4 p-6">
      <h1 className="text-title font-semibold">{t("audit.title")}</h1>
      <div className="flex flex-wrap items-center gap-3">
        <input
          type="search"
          aria-label={t("audit.filter")}
          placeholder={t("audit.filter")}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-72 rounded-control border border-rule bg-paper px-2 py-1"
        />
        <button
          type="button"
          disabled={rows.length === 0}
          onClick={() => downloadCsv(auditCsv(rows), `veyra-audit-${new Date().toISOString().slice(0, 10)}.csv`)}
          className="rounded-control border border-thread px-3 py-1 text-thread disabled:opacity-50"
        >
          {t("audit.export")}
        </button>
      </div>
      {audit.isPending ? (
        <p className="text-ink-2">{t("common.loading")}</p>
      ) : audit.isError ? (
        <p role="alert">{t("common.error", { message: audit.error.message })}</p>
      ) : rows.length === 0 ? (
        <p>{t("audit.empty")}</p>
      ) : (
        <DataTable columns={columns} rows={rows} rowKey={(r) => `${r.at}|${r.action}|${r.target}`} caption={t("audit.title")} />
      )}
    </div>
  );
}
