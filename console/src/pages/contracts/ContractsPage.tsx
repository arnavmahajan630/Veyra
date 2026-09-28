import { useNavigate } from "react-router";
import { useContracts } from "../../api/queries";
import type { ContractSummary } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { ageMs, formatAge } from "../../components/format";
import { useNow } from "../../components/useNow";
import { useI18n } from "../../i18n/i18n";
import { useTenantScope } from "../../shell/tenant";
import { ContractStateDot } from "./ContractStateDot";

const version = (v: number | null) => (v === null ? "—" : `v${v}`);

/** Every Log Contract in scope; Enter or a click opens its detail (C6). */
export default function ContractsPage() {
  const { t } = useI18n();
  const { scope } = useTenantScope();
  const contracts = useContracts(scope);
  const navigate = useNavigate();
  const now = useNow(10_000);

  const columns: Column<ContractSummary>[] = [
    {
      id: "contract",
      header: t("contracts.col.contract"),
      cell: (c) => <code className="font-mono text-meta">{c.id}</code>,
      sortValue: (c) => c.id,
    },
    { id: "tenant", header: t("contracts.col.tenant"), cell: (c) => <code className="font-mono text-meta">{c.tenant_id}</code>, sortValue: (c) => c.tenant_id },
    { id: "sources", header: t("contracts.col.sources"), cell: (c) => <code className="font-mono text-meta">{c.sources.join(", ")}</code> },
    { id: "active", header: t("contracts.col.active"), cell: (c) => version(c.active_version), align: "right" },
    { id: "canary", header: t("contracts.col.canary"), cell: (c) => version(c.canary_version), align: "right" },
    { id: "state", header: t("contracts.col.state"), cell: (c) => <ContractStateDot state={c.latest_state} />, sortValue: (c) => c.latest_state },
    {
      id: "changed",
      header: t("contracts.col.changed"),
      cell: (c) => {
        const age = ageMs(c.updated_at, now);
        return age === null ? "—" : formatAge(age);
      },
      sortValue: (c) => c.updated_at ?? "",
    },
    { id: "by", header: t("contracts.col.by"), cell: (c) => c.updated_by ?? "—" },
  ];

  return (
    <div className="flex flex-col gap-4 p-6">
      <h1 className="text-title font-semibold">{t("contracts.title")}</h1>
      {contracts.isPending ? (
        <p className="text-ink-2">{t("common.loading")}</p>
      ) : contracts.isError ? (
        <p role="alert">{t("common.error", { message: contracts.error.message })}</p>
      ) : contracts.data.length === 0 ? (
        <p>{t("contracts.empty")}</p>
      ) : (
        <DataTable
          columns={columns}
          rows={contracts.data}
          rowKey={(c) => c.id}
          onActivate={(c) => navigate(`/contracts/${encodeURIComponent(c.id)}`)}
          caption={t("contracts.title")}
        />
      )}
    </div>
  );
}
