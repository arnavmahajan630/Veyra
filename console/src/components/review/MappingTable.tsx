import { useState, type FocusEvent, type MouseEvent } from "react";
import type { DraftMapping, DraftTemplate } from "../../api/types";
import { useI18n } from "../../i18n/i18n";
import { MappingEditor } from "./MappingEditor";
import type { Row } from "./mappings";

export interface MappingTableProps {
  rows: Row[];
  template: DraftTemplate;
  editable: boolean;
  /** The hovered or focused row's span (null when it has none) and the row's rectangle, for the thread. */
  onHover: (spanId: string | null, rect: DOMRect | null) => void;
  onEdit: (index: number, mapping: DraftMapping) => void;
}

function StateCell({ row }: { row: Row }) {
  const { t } = useI18n();
  switch (row.state) {
    case "ok":
      return (
        <span aria-label={t("review.checked")} className="text-tier1">
          ✓
        </span>
      );
    case "failed":
      // One line, truncated with the full reason on hover, so a failure never grows the row (C6 AC5).
      return (
        <span className="block truncate" title={row.reason}>
          <span aria-hidden="true" className="text-tier4">
            ✗{" "}
          </span>
          {row.reason}
        </span>
      );
    case "review":
      // Turmeric is reserved for byte highlights (C5), so "review" is a highlight-token chip in ink.
      return <span className="rounded-control bg-highlight px-1 text-ink">{t("review.needsReview")}</span>;
    default:
      return <span className="text-ink-2">{t("review.derived")}</span>;
  }
}

/** One row per proposed mapping: path, value or constant, provenance state, and an inline editor. */
export function MappingTable({ rows, template, editable, onHover, onEdit }: MappingTableProps) {
  const { t } = useI18n();
  const [editing, setEditing] = useState<number | null>(null);
  const report = (row: Row) => (event: MouseEvent<HTMLTableRowElement> | FocusEvent<HTMLTableRowElement>) =>
    onHover(row.spanId ?? null, event.currentTarget.getBoundingClientRect());

  return (
    <table className="w-full table-fixed border-collapse text-body">
      <caption className="sr-only">{t("review.mapping")}</caption>
      <tbody>
        {rows.map((row, index) => (
          <tr
            key={`${index}-${row.ocsf_path}`}
            className="h-10 border-b border-rule"
            onMouseEnter={report(row)}
            onFocus={report(row)}
            onMouseLeave={() => onHover(null, null)}
          >
            <th scope="row" className="w-2/5 truncate py-1 pr-3 text-left font-mono text-meta font-normal">
              {row.ocsf_path}
            </th>
            {editing === index ? (
              <td colSpan={3}>
                <MappingEditor
                  template={template}
                  row={row}
                  taken={new Set(rows.filter((_, other) => other !== index).map((r) => r.ocsf_path))}
                  onApply={(mapping) => {
                    setEditing(null);
                    onEdit(index, mapping);
                  }}
                  onCancel={() => setEditing(null)}
                />
              </td>
            ) : (
              <>
                <td className="truncate py-1 pr-3 font-mono text-meta">
                  <span aria-hidden="true" className="text-ink-2">
                    {row.const !== undefined ? "= " : "← "}
                  </span>
                  <span>{row.value}</span>
                </td>
                <td className="w-1/4 py-1 pr-3 text-meta">
                  <StateCell row={row} />
                </td>
                <td className="w-16 py-1 text-right">
                  {editable ? (
                    <button type="button" onClick={() => setEditing(index)} className="text-meta text-thread hover:underline">
                      {t("review.edit")}
                    </button>
                  ) : null}
                </td>
              </>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
