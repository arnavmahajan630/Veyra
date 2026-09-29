import type { DiffOut } from "../../api/types";
import { useI18n } from "../../i18n/i18n";

function IdList({ title, ids }: { title: string; ids: readonly string[] }) {
  if (ids.length === 0) return null;
  return (
    <div>
      <h3 className="font-medium">{title}</h3>
      <ul className="mt-1 flex flex-col gap-0.5">
        {ids.map((id) => (
          <li key={id} className="font-mono text-meta">
            {id}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The semantic diff first (templates added, removed, changed), then the YAML lines (C6). */
export function DiffView({ diff }: { diff: DiffOut }) {
  const { t } = useI18n();
  const { semantic } = diff;
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <IdList title={t("contracts.templatesAdded")} ids={semantic.templates_added} />
        <IdList title={t("contracts.templatesRemoved")} ids={semantic.templates_removed} />
        <IdList title={t("contracts.templatesChanged")} ids={semantic.templates_changed.map((c) => c.id)} />
      </div>
      <pre aria-label={t("contracts.yamlDiff")} className="overflow-x-auto border border-rule p-2 font-mono text-meta">
        {diff.yaml.split("\n").map((line, index) => (
          <span
            key={index}
            className={`block ${line.startsWith("+") ? "bg-tier1/10 text-ink" : line.startsWith("-") ? "bg-tier4/10 text-ink" : "text-ink-2"}`}
          >
            {line || " "}
          </span>
        ))}
      </pre>
    </div>
  );
}
