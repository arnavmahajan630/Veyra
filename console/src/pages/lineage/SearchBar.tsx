import { Search } from "lucide-react";
import { type ChangeEvent, type FormEvent } from "react";
import { useI18n } from "../../i18n/i18n";

export interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: () => void;
  loading?: boolean;
}

export function SearchBar({ value, onChange, onSubmit, loading }: SearchBarProps) {
  const { t } = useI18n();

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit?.();
  };

  return (
    <form role="search" onSubmit={handleSubmit} className="relative w-full max-w-2xl">
      <div className="relative flex items-center">
        <Search className="absolute left-3.5 h-4 w-4 text-ink-3" aria-hidden="true" />
        <input
          type="search"
          value={value}
          onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
          placeholder={t("lineage.search.placeholder") || "Search by event UID, IP, user, SHA-256 or template sig..."}
          className="h-10 w-full rounded-md border border-edge bg-surface-1 pl-10 pr-10 text-body text-ink placeholder:text-ink-3 focus:border-turmeric focus:outline-none focus:ring-1 focus:ring-turmeric"
          aria-label={t("lineage.search.label") || "Search lineage"}
        />
        {loading && (
          <div className="absolute right-3 h-4 w-4 animate-spin rounded-full border-2 border-turmeric border-t-transparent" />
        )}
      </div>
    </form>
  );
}
