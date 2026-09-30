import { AlertTriangle, Check, ChevronDown, ChevronRight } from "lucide-react";
import { useImperativeHandle, useRef, useState, type KeyboardEvent, type Ref } from "react";
import type { EventRevision } from "../../api/types";
import { useI18n } from "../../i18n/i18n";
import {
  buildFields,
  observableEntries,
  unmappedEntries,
  type NormalizedField,
} from "./fields";

export interface NormalizedPaneHandle {
  getRowRect(key: string): DOMRect | null;
}

export interface NormalizedPaneProps {
  revision: EventRevision;
  /** The decoded raw bytes, so each located field can be checked against them. */
  rawText?: string | null;
  activeKey?: string | null;
  pinnedKey?: string | null;
  onFieldHover: (key: string | null, span?: [number, number]) => void;
  onFieldPin: (key: string | null, span?: [number, number]) => void;
  ref?: Ref<NormalizedPaneHandle>;
}

export function NormalizedPane({
  revision,
  rawText,
  activeKey,
  pinnedKey,
  onFieldHover,
  onFieldPin,
  ref,
}: NormalizedPaneProps) {
  const { t } = useI18n();
  const rowRefs = useRef<Record<string, HTMLElement | null>>({});
  const [showUnmapped, setShowUnmapped] = useState(false);
  const [showObservables, setShowObservables] = useState(false);
  const [showUlpf, setShowUlpf] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState(0);

  useImperativeHandle(ref, () => ({
    getRowRect(key: string) {
      const el = rowRefs.current[key];
      return el ? el.getBoundingClientRect() : null;
    },
  }));

  const fields = buildFields(revision, rawText);
  const observables = observableEntries(revision);
  const unmapped = unmappedEntries(revision);

  const move = (delta: number) => {
    if (fields.length === 0) return;
    const next = (focusedIndex + delta + fields.length) % fields.length;
    setFocusedIndex(next);
    const field = fields[next];
    if (field) {
      onFieldHover(field.path, field.byteSpan);
      rowRefs.current[field.path]?.focus();
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      move(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      move(-1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      const field = fields[focusedIndex];
      if (field) onFieldPin(field.path === pinnedKey ? null : field.path, field.byteSpan);
    }
  };

  return (
    <div
      onKeyDown={handleKeyDown}
      role="listbox"
      aria-label={t("lineage.event.normalized") || "Normalized fields"}
      className="flex h-full flex-col rounded-md border border-edge bg-surface-1"
    >
      <div className="flex items-center justify-between border-b border-edge bg-surface-2 px-3 py-2 text-meta font-medium text-ink-2">
        <span className="font-semibold uppercase tracking-wider text-micro text-ink">
          {t("lineage.event.normalizedTitle") || "Normalized (OCSF)"}
        </span>
        <span className="font-mono text-micro text-ink-3">
          {t("lineage.event.revShort") || "rev"} {revision.revision}
        </span>
      </div>

      <div className="flex-1 divide-y divide-edge overflow-auto">
        {fields.length === 0 && (
          <p className="p-3 text-meta text-ink-3">
            {t("lineage.event.noFields") || "This revision carries no mapped fields."}
          </p>
        )}

        {fields.map((field, index) => (
          <FieldRow
            key={field.path}
            field={field}
            selected={pinnedKey === field.path}
            active={activeKey === field.path}
            tabIndex={focusedIndex === index ? 0 : -1}
            registerRef={(el) => {
              rowRefs.current[field.path] = el;
            }}
            onFocus={() => setFocusedIndex(index)}
            onHover={() => {
              setFocusedIndex(index);
              onFieldHover(field.path, field.byteSpan);
            }}
            onLeave={() => onFieldHover(null)}
            onPin={() =>
              onFieldPin(pinnedKey === field.path ? null : field.path, field.byteSpan)
            }
          />
        ))}

        <Section
          open={showObservables}
          onToggle={() => setShowObservables(!showObservables)}
          label={`${t("lineage.event.observables") || "Observables"} (${observables.length})`}
        >
          {observables.length === 0 ? (
            <p>{t("lineage.event.noObservables") || "No observables on this revision."}</p>
          ) : (
            <ul className="space-y-1">
              {observables.map((o) => (
                <li key={o.name}>
                  <span className="text-ink-3">{o.name}</span>{" "}
                  <span className="text-ink">{o.value}</span>
                  {o.type && <span className="text-ink-3"> · {o.type}</span>}
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section
          open={showUnmapped}
          onToggle={() => setShowUnmapped(!showUnmapped)}
          label={`${t("lineage.event.unmapped") || "Unmapped"} (${unmapped.length})`}
        >
          {unmapped.length === 0 ? (
            <p>{t("lineage.event.noUnmapped") || "No unmapped fields on this revision."}</p>
          ) : (
            <ul className="space-y-1">
              {unmapped.map(([key, value]) => (
                <li key={key}>
                  <span className="text-ink-3">{key}</span>{" "}
                  <span className="text-ink">{String(value)}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section
          open={showUlpf}
          onToggle={() => setShowUlpf(!showUlpf)}
          label={t("lineage.event.ulpf") || "ULPF lineage"}
        >
          <pre className="max-h-48 overflow-auto">
            {JSON.stringify(
              {
                field_offsets: revision.field_offsets ?? {},
                derived_fields: revision.derived_fields ?? {},
                ulpf: (revision.ocsf ?? {})["ulpf"] ?? null,
              },
              null,
              2,
            )}
          </pre>
        </Section>
      </div>
    </div>
  );
}

function Section({
  open,
  onToggle,
  label,
  children,
}: {
  open: boolean;
  onToggle: () => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-meta font-medium text-ink-3 hover:bg-surface-2"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" />
        )}
        <span>{label}</span>
      </button>
      {open && (
        <div className="bg-surface-2/30 p-3 font-mono text-micro text-ink-2">{children}</div>
      )}
    </div>
  );
}

function FieldRow({
  field,
  selected,
  active,
  tabIndex,
  registerRef,
  onFocus,
  onHover,
  onLeave,
  onPin,
}: {
  field: NormalizedField;
  selected: boolean;
  active: boolean;
  tabIndex: number;
  registerRef: (el: HTMLDivElement | null) => void;
  onFocus: () => void;
  onHover: () => void;
  onLeave: () => void;
  onPin: () => void;
}) {
  const { t } = useI18n();
  return (
    <div
      ref={registerRef}
      role="option"
      aria-selected={selected}
      tabIndex={tabIndex}
      data-field={field.path}
      onFocus={onFocus}
      onMouseEnter={onHover}
      onMouseLeave={onLeave}
      onClick={onPin}
      className={`flex cursor-pointer items-center justify-between gap-4 px-3 py-2 text-body transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-turmeric ${
        selected
          ? "border-l-2 border-turmeric bg-highlight/50"
          : active
            ? "bg-surface-2"
            : "hover:bg-surface-2/60"
      }`}
    >
      <span className="font-mono text-meta font-medium text-ink-2">{field.path}</span>
      <span className="flex items-center gap-2.5">
        <span className="font-mono text-body font-semibold text-ink">{field.value}</span>
        {field.provenance !== "extracted" && (
          <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-micro text-ink-3">
            {t(`lineage.provenance.${field.provenance}`) || field.provenance}
          </span>
        )}
        {field.verified === true && (
          <Check
            className="h-3.5 w-3.5 text-emerald-500"
            aria-label={t("lineage.event.provenanceOk") || "provenance verified"}
          />
        )}
        {field.verified === false && (
          <span title={field.reason}>
            <AlertTriangle
              className="h-3.5 w-3.5 text-rose-500"
              aria-label={t("lineage.event.provenanceFailed") || "provenance failed"}
            />
          </span>
        )}
      </span>
    </div>
  );
}
