import { useState } from "react";
import type { DraftMapping, DraftTemplate } from "../../api/types";
import { useI18n } from "../../i18n/i18n";
import type { Row } from "./mappings";

export interface MappingEditorProps {
  template: DraftTemplate;
  row: Row;
  /** Fields other rows already map: control-api refuses a field mapped twice, so they aren't offered. */
  taken: ReadonlySet<string>;
  onApply: (mapping: DraftMapping) => void;
  onCancel: () => void;
}

const CONTROL = "rounded-control border border-rule bg-paper px-2 py-0.5 text-meta";

/** Inline editor for one mapping row: an OCSF field, a token chip, or an enum constant. */
export function MappingEditor({ template, row, taken, onApply, onCancel }: MappingEditorProps) {
  const { t } = useI18n();
  const [path, setPath] = useState(row.ocsf_path);
  const [token, setToken] = useState<string | undefined>(row.token);
  const [constant, setConstant] = useState<number | undefined>(row.const);
  const enumValues = template.request.enums[path];
  const apply = () =>
    onApply(enumValues && constant !== undefined ? { ocsf_path: path, const: constant } : { ocsf_path: path, token });

  return (
    <div className="flex flex-wrap items-center gap-2 py-1">
      <select aria-label={t("review.field")} value={path} onChange={(e) => setPath(e.target.value)} className={CONTROL}>
        {template.request.allowed_fields.filter((field) => !taken.has(field)).map((field) => (
          <option key={field} value={field}>
            {field}
          </option>
        ))}
      </select>
      {template.spans.map((span) => (
        <button
          key={span.id}
          type="button"
          aria-pressed={token === span.id && constant === undefined}
          onClick={() => {
            setToken(span.id);
            setConstant(undefined);
          }}
          className="rounded-control border border-rule px-1.5 font-mono text-meta aria-pressed:border-thread aria-pressed:text-thread"
        >
          {span.value}
        </button>
      ))}
      {enumValues ? (
        <select
          aria-label={t("review.value")}
          value={constant ?? ""}
          onChange={(e) => setConstant(e.target.value === "" ? undefined : Number(e.target.value))}
          className={CONTROL}
        >
          <option value="">—</option>
          {Object.entries(enumValues).map(([value, label]) => (
            <option key={value} value={value}>
              {value} {label}
            </option>
          ))}
        </select>
      ) : null}
      <button type="button" onClick={apply} className="rounded-control bg-thread px-2 py-0.5 text-meta text-paper">
        {t("review.apply")}
      </button>
      <button type="button" onClick={onCancel} className="text-meta text-thread hover:underline">
        {t("review.cancel")}
      </button>
    </div>
  );
}
