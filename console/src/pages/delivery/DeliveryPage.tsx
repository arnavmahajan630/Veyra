import { useOverview, useRoutes } from "../../api/queries";
import type { OverviewRoute, RouteSpec } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { StatusDot, type StatusTone } from "../../components/StatusDot";
import { useI18n } from "../../i18n/i18n";
import { useTenantScope } from "../../shell/tenant";

interface RouteRow {
  spec: RouteSpec;
  stats: OverviewRoute | undefined;
}

const BREAKER: Record<string, StatusTone> = { closed: "good", half_open: "warn", open: "bad" };
const list = (value: unknown) => (Array.isArray(value) ? value.join(", ") : null);

/** Delivery (C6, TC44): each route's definition joined by id with its live statistics. */
export default function DeliveryPage() {
  const { t } = useI18n();
  const { scope } = useTenantScope();
  const routes = useRoutes();
  const overview = useOverview(scope);
  const stats = new Map((overview.data?.routes ?? []).map((r) => [r.route_id, r]));
  const rows: RouteRow[] = (routes.data?.routes ?? []).map((spec) => ({ spec, stats: stats.get(spec.id) }));

  const filterSummary = (spec: RouteSpec) => {
    const tenants = list(spec.filter.tenants);
    const tiers = list(spec.filter.tiers);
    const classes = list(spec.filter.classes);
    return [
      tenants === "*" ? t("delivery.allTenants") : tenants ? t("delivery.filterTenants", { list: tenants }) : null,
      tiers ? t("delivery.filterTiers", { list: tiers }) : null,
      classes ? t("delivery.filterClasses", { list: classes }) : null,
    ]
      .filter(Boolean)
      .join("; ");
  };
  const sink = (spec: RouteSpec) => {
    const target = spec.sink.path ?? spec.sink.host ?? "";
    return `${String(spec.sink.type ?? "")} ${String(target)}`.trim();
  };
  const masking = (spec: RouteSpec) =>
    spec.masking === "none" ? "none" : Object.entries(spec.masking).map(([path, how]) => `${path}: ${how}`).join(", ");

  const columns: Column<RouteRow>[] = [
    {
      id: "route",
      header: t("delivery.col.route"),
      cell: (r) => (
        <>
          <code className="font-mono text-meta">{r.spec.id}</code>
          <div className="text-meta text-ink-2">{filterSummary(r.spec)}</div>
        </>
      ),
      sortValue: (r) => r.spec.id,
    },
    { id: "format", header: t("delivery.col.format"), cell: (r) => <code className="font-mono text-meta">{r.spec.format}</code> },
    { id: "masking", header: t("delivery.col.masking"), cell: (r) => <code className="font-mono text-meta">{masking(r.spec)}</code> },
    { id: "sink", header: t("delivery.col.sink"), cell: (r) => <code className="wrap-anywhere font-mono text-meta">{sink(r.spec)}</code> },
    { id: "delivered", header: t("delivery.col.delivered"), cell: (r) => r.stats?.delivered_per_min ?? "—", align: "right", sortValue: (r) => r.stats?.delivered_per_min ?? -1 },
    { id: "failed", header: t("delivery.col.failed"), cell: (r) => r.stats?.failed_per_min ?? "—", align: "right" },
    { id: "lag", header: t("delivery.col.lag"), cell: (r) => (r.stats?.lag_s == null ? "—" : `${r.stats.lag_s} s`), align: "right" }, // no break between number and unit
    {
      id: "breaker",
      header: t("delivery.col.breaker"),
      cell: (r) => {
        const state = r.stats?.breaker ?? null;
        return <StatusDot tone={state ? (BREAKER[state] ?? "idle") : "idle"} label={t(`delivery.breaker.${state ?? "unknown"}`)} />;
      },
    },
  ];

  return (
    <div className="flex flex-col gap-4 p-6">
      <h1 className="text-title font-semibold">{t("delivery.title")}</h1>
      {routes.isPending ? (
        <p className="text-ink-2">{t("common.loading")}</p>
      ) : routes.isError ? (
        <p role="alert">{t("common.error", { message: routes.error.message })}</p>
      ) : (
        <DataTable columns={columns} rows={rows} rowKey={(r) => r.spec.id} caption={t("delivery.title")} />
      )}
      <p className="text-meta text-ink-2">{t("delivery.receiptsNote")}</p>
    </div>
  );
}
