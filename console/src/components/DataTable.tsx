import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

export interface Column<T> {
  id: string;
  header: string;
  cell: (row: T) => ReactNode;
  sortValue?: (row: T) => string | number;
  align?: "left" | "right";
}

export interface DataTableProps<T> {
  columns: readonly Column<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
  onActivate?: (row: T) => void;
  rowClassName?: (row: T) => string;
  caption?: string;
}

type Sort = { id: string; dir: "asc" | "desc" } | null;

export function DataTable<T>({ columns, rows, rowKey, onActivate, rowClassName, caption }: DataTableProps<T>) {
  const [sort, setSort] = useState<Sort>(null);
  const rowRefs = useRef<Array<HTMLTableRowElement | null>>([]);

  const sorted = useMemo(() => {
    const column = sort ? columns.find((c) => c.id === sort.id) : undefined;
    const value = column?.sortValue;
    if (!sort || !value) return rows;
    const factor = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const x = value(a);
      const y = value(b);
      return (x < y ? -1 : x > y ? 1 : 0) * factor;
    });
  }, [rows, columns, sort]);

  const toggle = (id: string) =>
    setSort((current) =>
      current?.id === id ? { id, dir: current.dir === "asc" ? "desc" : "asc" } : { id, dir: "asc" },
    );

  const onKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, index: number, row: T) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      rowRefs.current[index + 1]?.focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      rowRefs.current[index - 1]?.focus();
    } else if (event.key === "Enter" && onActivate) {
      event.preventDefault();
      onActivate(row);
    }
  };

  return (
    <table className="w-full border-collapse text-left">
      {caption ? <caption className="sr-only">{caption}</caption> : null}
      <thead className="sticky top-0 bg-paper">
        <tr className="border-b border-rule">
          {columns.map((column) => (
            <th
              key={column.id}
              scope="col"
              aria-sort={sort?.id === column.id ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
              className={`px-3 py-2 text-meta font-medium text-ink-2 ${column.align === "right" ? "text-right" : ""}`}
            >
              {column.sortValue ? (
                <button type="button" className="hover:text-ink" onClick={() => toggle(column.id)}>
                  {column.header}
                </button>
              ) : (
                column.header
              )}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.map((row, index) => (
          <tr
            key={rowKey(row)}
            ref={(element) => {
              rowRefs.current[index] = element;
            }}
            tabIndex={0}
            onKeyDown={(event) => onKeyDown(event, index, row)}
            onClick={onActivate ? () => onActivate(row) : undefined}
            className={`border-b border-rule focus-visible:bg-highlight/30 ${
              onActivate ? "cursor-pointer hover:bg-rule/30" : ""
            } ${rowClassName?.(row) ?? ""}`}
          >
            {columns.map((column) => (
              <td
                key={column.id}
                className={`px-3 py-2 align-top ${column.align === "right" ? "text-right tabular-nums" : ""}`}
              >
                {column.cell(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
