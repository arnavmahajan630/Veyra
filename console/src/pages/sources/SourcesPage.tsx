import { Link, useSearchParams } from "react-router";
import { useSourceHealth, useSources } from "../../api/queries";
import { DataTable, type Column } from "../../components/DataTable";
import { ageMs, formatAge, formatEps } from "../../components/format";
import { MixBar } from "../../components/MixBar";
import { useNow } from "../../components/useNow";
import { useI18n } from "../../i18n/i18n";
import { useTenantScope } from "../../shell/tenant";
import { SourceDrawer } from "./SourceDrawer";
import { buildRows, epsDelta, formatSkew, type SourceRow } from "./sourceRows";

export default function SourcesPage() {
  const { t } = useI18n();
  const { scope } = useTenantScope();
  const sources = useSources(scope);
  const health = useSourceHealth(scope);
  const now = useNow();
  const [params, setParams] = useSearchParams();

  if (sources.isPending) return <p className="p-6 text-ink-2">{t("common.loading")}</p>;
  if (sources.isError) {
    return (
      <p role="alert" className="p-6">
        {t("common.error", { message: sources.error.message })}
      </p>
    );
  }

  const rows = buildRows(sources.data, health.data, now);
  const open = rows.find((r) => r.source.id === params.get("source"));

  const columns: Column<SourceRow>[] = [
    {
      id: "source",
      header: t("sources.col.source"),
      cell: (r) => (
        <>
          <div>{r.source.name}</div>
          <code className="font-mono text-meta text-ink-2">{r.source.id}</code>
        </>
      ),
      sortValue: (r) => r.source.name,
    },
    {
      id: "tenant",
      header: t("sources.col.tenant"),
      cell: (r) => <code className="font-mono text-meta">{r.source.tenant_id}</code>,
      sortValue: (r) => r.source.tenant_id,
    },
    { id: "zone", header: t("sources.col.zone"), cell: (r) => r.source.zone, sortValue: (r) => r.source.zone },
    { id: "transport", header: t("sources.col.transport"), cell: (r) => r.source.transport },
    {
      id: "contract",
      header: t("sources.col.contract"),
      cell: (r) => {
        const ref = r.health?.contract_ref ?? r.source.contract_id;
        return ref ? <code className="font-mono text-meta">{ref}</code> : "—";
      },
    },
    {
      id: "eps",
      header: t("sources.col.eps"),
      align: "right",
      sortValue: (r) => r.health?.actual_eps ?? 0,
      cell: (r) => {
        const actual = r.health?.actual_eps ?? 0;
        const delta = epsDelta(actual, r.source.expected_eps);
        return (
          <>
            <span>{`${formatEps(actual)} (${formatEps(r.source.expected_eps)})`}</span>
            {delta ? <span className="ml-2 text-meta text-ink-2">{delta}</span> : null}
          </>
        );
      },
    },
    {
      id: "seen",
      header: t("sources.col.lastSeen"),
      cell: (r) => {
        const age = ageMs(r.health?.last_seen ?? null, now);
        return age === null ? t("sources.never") : formatAge(age);
      },
    },
    { id: "tiers", header: t("sources.col.tiers"), cell: (r) => (r.health ? <MixBar counts={r.health.tiers} /> : null) },
    { id: "skew", header: t("sources.col.skew"), align: "right", cell: (r) => formatSkew(r.health?.clock_skew_p50_ms) },
    {
      id: "status",
      header: t("sources.col.status"),
      cell: (r) =>
        r.silentMs === null ? (
          t(`sources.status.${r.source.status}`)
        ) : (
          <span className="font-medium">{t("sources.silent", { age: formatAge(r.silentMs) })}</span>
        ),
    },
  ];

  return (
    <div className="flex flex-col gap-4 p-6">
      <h1 className="text-title font-semibold">{t("sources.title")}</h1>
      {rows.length === 0 ? (
        <p>
          <Link to="/onboard" className="text-thread underline">
            {t("overview.noSources")}
          </Link>
        </p>
      ) : (
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => r.source.id}
          onActivate={(r) => setParams({ source: r.source.id })}
          rowClassName={(r) => (r.silentMs === null ? "" : "bg-tier2/15")}
          caption={t("sources.title")}
        />
      )}
      {open ? <SourceDrawer row={open} onClose={() => setParams({})} /> : null}
    </div>
  );
}
