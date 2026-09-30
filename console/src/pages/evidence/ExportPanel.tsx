import { Download, FileArchive, Terminal } from "lucide-react";
import { useState } from "react";
import { useEvidenceExport, useLineageSearch } from "../../api/queries";
import { useI18n } from "../../i18n/i18n";
import { useTenantScope } from "../../shell/tenant";

export function ExportPanel() {
  const { t } = useI18n();
  const { scope } = useTenantScope();
  const [eventUid, setEventUid] = useState("");
  // Pick an event by searching, as the phase file asks — a bare UUID box means the
  // presenter has to copy a uid from somewhere else mid-demo.
  const [query, setQuery] = useState("");
  const { data: results, isFetching } = useLineageSearch(query, scope);
  const exportMutation = useEvidenceExport();

  const handleDownload = async () => {
    if (!eventUid.trim()) return;
    try {
      const blob = await exportMutation.mutateAsync(eventUid.trim());
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `veyra-evidence-${eventUid.trim()}.zip`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
    } catch {
      // Handled by mutation error state
    }
  };

  return (
    <div className="rounded-md border border-edge bg-surface-1 p-4 space-y-4">
      <div className="flex items-center gap-2">
        <FileArchive className="h-4 w-4 text-turmeric" />
        <h3 className="text-meta font-semibold text-ink">
          {t("evidence.export.title") || "Download Evidence Package"}
        </h3>
      </div>

      <p className="text-meta text-ink-3">
        {t("evidence.export.desc") ||
          "Export an auditor-ready standalone ZIP archive containing the exact raw bytes, envelope metadata, Merkle inclusion proof, signed window root, public key, and self-contained verify.py script."}
      </p>

      <div className="space-y-2">
        <label className="block text-meta text-ink-2" htmlFor="evidence-export-search">
          {t("evidence.export.find") || "Find an event"}
        </label>
        <input
          id="evidence-export-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("evidence.export.searchPlaceholder") || "Event UID, IP, user or SHA-256 prefix"}
          className="h-9 w-full rounded-md border border-edge bg-surface-2 px-3 text-meta text-ink placeholder:text-ink-3 focus:border-turmeric focus:outline-none sm:w-96"
        />
        {query && (
          <ul className="max-h-40 divide-y divide-edge overflow-auto rounded border border-edge">
            {isFetching && (results?.hits ?? []).length === 0 && (
              <li className="px-3 py-2 text-micro text-ink-3">{t("common.loading") || "Searching…"}</li>
            )}
            {!isFetching && (results?.hits ?? []).length === 0 && (
              <li className="px-3 py-2 text-micro text-ink-3">
                {t("evidence.export.noMatches") || "No event matches that."}
              </li>
            )}
            {(results?.hits ?? []).map((hit) => (
              <li key={hit.event_uid}>
                <button
                  type="button"
                  onClick={() => setEventUid(hit.event_uid)}
                  className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-micro hover:bg-surface-2 ${
                    eventUid === hit.event_uid ? "bg-highlight/40 font-semibold" : ""
                  }`}
                >
                  <span className="font-mono text-ink">{hit.event_uid}</span>
                  <span className="truncate text-ink-3">{hit.raw_preview}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-micro text-ink-2">
          {eventUid || (t("evidence.export.noneSelected") || "No event selected")}
        </span>
        <button
          type="button"
          onClick={handleDownload}
          disabled={!eventUid.trim() || exportMutation.isPending}
          className="flex items-center gap-2 rounded-md bg-turmeric px-3.5 py-1.5 text-meta font-semibold text-surface-1 transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          <Download className="h-4 w-4" />
          <span>
            {exportMutation.isPending
              ? t("common.downloading") || "Generating ZIP..."
              : t("evidence.export.btn") || "Download evidence package"}
          </span>
        </button>
      </div>

      {exportMutation.isError && (
        <p className="rounded-md border border-rose-500/30 bg-rose-500/5 p-3 text-meta text-rose-600">
          {(t("evidence.export.failed") || "The export failed: {reason}").replace(
            "{reason}",
            exportMutation.error instanceof Error ? exportMutation.error.message : "unknown error",
          )}
        </p>
      )}

      <div className="space-y-1.5 rounded border border-edge bg-surface-2/40 p-3 text-meta text-ink-2">
        <div className="flex items-center gap-2 text-ink font-medium">
          <Terminal className="h-4 w-4 text-turmeric" />
          <span>{t("evidence.export.offline") || "Offline independent verification"}</span>
        </div>
        <div className="font-mono text-micro text-ink bg-surface-1 p-2 rounded border border-edge select-all">
          unzip veyra-evidence-&lt;uid&gt;.zip &amp;&amp; python3 verify.py
        </div>
        <p className="text-micro text-ink-3">
          {t("evidence.export.offlineNote") ||
            "No dependencies: the Python standard library only, with pure-Python Ed25519 verification."}
        </p>
      </div>
    </div>
  );
}
