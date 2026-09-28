import { useI18n } from "../i18n/i18n";

/** A nav entry whose page another phase builds (C6, B6, B7); `data-phase` names the owner for developers. */
export function PlaceholderPage({ labelKey, phase }: { labelKey: string; phase: string }) {
  const { t } = useI18n();
  return (
    <div className="p-6" data-phase={phase}>
      <h1 className="text-title font-semibold">{t(labelKey)}</h1>
      <p className="mt-2 text-ink-2">{t("common.notBuilt")}</p>
    </div>
  );
}
