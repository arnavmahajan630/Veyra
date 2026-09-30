import { useState } from "react";
import { useSearchParams } from "react-router";
import { useLineageSearch } from "../../api/queries";
import { useI18n } from "../../i18n/i18n";
import { useTenantScope } from "../../shell/tenant";
import { ResultsList } from "./ResultsList";
import { SearchBar } from "./SearchBar";

export default function LineagePage() {
  const { t } = useI18n();
  // /lineage/:uid is its own route in AppShell, so this page is only ever the search view.
  const { scope } = useTenantScope();
  const [searchParams, setSearchParams] = useSearchParams();

  const initialQuery = searchParams.get("q") || "";
  const [query, setQuery] = useState(initialQuery);
  const [searchedQuery, setSearchedQuery] = useState(initialQuery);

  const { data: searchResult, isLoading } = useLineageSearch(searchedQuery, scope);

  const handleSearchSubmit = () => {
    setSearchedQuery(query);
    if (query) {
      setSearchParams({ q: query });
    } else {
      setSearchParams({});
    }
  };

  const handleQuickSearch = (term: string) => {
    setQuery(term);
    setSearchedQuery(term);
    setSearchParams({ q: term });
  };

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-title font-semibold text-ink">
          {t("lineage.title") || "Lineage Explorer"}
        </h1>
        <p className="mt-1 text-body text-ink-3">
          {t("lineage.description") ||
            "Trace any normalized event back to its raw envelope, byte offsets, and cryptographic proof."}
        </p>
      </div>

      <div className="space-y-2">
        <SearchBar
          value={query}
          onChange={setQuery}
          onSubmit={handleSearchSubmit}
          loading={isLoading}
        />

        {/* Quick query tags */}
        <div className="flex flex-wrap items-center gap-2 pt-1 text-meta text-ink-3">
          <span>{t("lineage.quickSearch") || "Quick search:"}</span>
          {["103.21.4.77", "a.sharma", "45.12.3.9", "authsrv"].map((term) => (
            <button
              key={term}
              type="button"
              onClick={() => handleQuickSearch(term)}
              className="rounded bg-surface-2 px-2 py-0.5 font-mono text-micro text-ink-2 hover:bg-surface-3 transition-colors"
            >
              {term}
            </button>
          ))}
        </div>
      </div>

      {searchedQuery ? (
        <ResultsList hits={searchResult?.hits ?? []} />
      ) : (
        <div className="rounded-md border border-edge bg-surface-1 p-12 text-center text-ink-3">
          {t("lineage.searchHint") ||
            "Search by event UID, client IP, username, SHA-256 hash, or template signature to begin tracing."}
        </div>
      )}
    </div>
  );
}
