import { Link, useNavigate } from "react-router";
import { useOverview, useSources } from "../../api/queries";
import type { OverviewRoute, OverviewSource } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { ageMs, formatAge, formatEps, parseTime } from "../../components/format";
import { MixBar } from "../../components/MixBar";
import { StatusDot } from "../../components/StatusDot";
import { TierBar } from "../../components/TierBar";
import { TierChip } from "../../components/TierChip";
import { TIERS, percent, tierShares } from "../../components/tiers";
import { useI18n } from "../../i18n/i18n";
import { useTenantScope } from "../../shell/tenant";
import { PipelineFlow } from "./PipelineFlow";
import { BUCKET_COUNT, useTierBuckets } from "./useTierSeries";

function RouteRow({ route }: { route: OverviewRoute }) {
  const { t } = useI18n();
  return (
    <li className="flex flex-wrap items-center gap-x-5 py-2">
      <code className="font-mono text-meta">{route.route_id}</code>
      <span className="tabular-nums">{t("overview.perMin", { n: route.delivered_per_min })}</span>
      {route.failed_per_min > 0 ? (
        <span className="tabular-nums">{t("overview.failedPerMin", { n: route.failed_per_min })}</span>
      ) : null}
      {route.lag_s !== null ? (
        <span className="tabular-nums text-ink-2">{t("overview.lag", { n: route.lag_s })}</span>
      ) : null}
      {route.breaker === "open" ? <StatusDot tone="bad" label={t("overview.breakerOpen")} /> : null}
    </li>
  );
}

export default function OverviewPage() {
  const { t } = useI18n();
  const { scope } = useTenantScope();
  const overview = useOverview(scope);
  const series = useTierBuckets(overview.data, scope);
  // Names come from control-api; until they load (or for a source it doesn't know) the id stands in.
  const sourceList = useSources(scope);
  const names = new Map(sourceList.data?.map((s) => [s.id, s.name]));
  const navigate = useNavigate();

  if (overview.isPending) return <p className="p-6 text-ink-2">{t("common.loading")}</p>;
  if (overview.isError) {
    return (
      <p role="alert" className="p-6">
        {t("common.error", { message: overview.error.message })}
      </p>
    );
  }

  const data = overview.data;
  const asOf = parseTime(data.as_of);
  const sealAge = ageMs(data.vault.last_sealed_at, asOf);
  const shares = tierShares(data.totals_by_tier);
  const root = data.vault.last_root;

  const columns: Column<OverviewSource>[] = [
    {
      id: "source",
      header: t("sources.col.source"),
      cell: (s) => (
        <>
          <div>{names.get(s.source_id) ?? s.source_id}</div>
          {names.has(s.source_id) ? (
            <code className="font-mono text-meta text-ink-2">{s.source_id}</code>
          ) : null}
        </>
      ),
      sortValue: (s) => names.get(s.source_id) ?? s.source_id,
    },
    { id: "zone", header: t("sources.col.zone"), cell: (s) => s.zone, sortValue: (s) => s.zone },
    { id: "tiers", header: t("sources.col.tiers"), cell: (s) => <MixBar counts={s.tiers} /> },
    {
      id: "eps",
      header: t("sources.col.actualEps"),
      cell: (s) => formatEps(s.eps),
      sortValue: (s) => s.eps,
      align: "right",
    },
    {
      id: "seen",
      header: t("sources.col.lastSeen"),
      cell: (s) => {
        const age = ageMs(s.last_seen, asOf);
        return age === null ? t("sources.never") : formatAge(age);
      },
    },
  ];

  return (
    <div className="flex flex-col gap-8 p-6">
      <h1 className="text-title font-semibold">{t("overview.title")}</h1>

      {data.sources.length === 0 ? (
        <p>
          <Link to="/onboard" className="text-thread underline">
            {t("overview.noSources")}
          </Link>
        </p>
      ) : null}

      <PipelineFlow overview={data} />

      <section aria-labelledby="overview-tiers">
        <h2 id="overview-tiers" className="mb-2 text-lead font-semibold">
          {t("overview.tierMix")}
        </h2>
        <TierBar series={series} capacity={BUCKET_COUNT} />
        <ul className="mt-2 flex flex-wrap gap-x-6 gap-y-1">
          {TIERS.map((tier) => (
            <li key={tier} className="flex items-center gap-2">
              <TierChip tier={tier} />
              <span className="tabular-nums">{percent(shares[tier])}</span>
            </li>
          ))}
        </ul>
      </section>

      {data.sources.length > 0 ? (
        <section aria-labelledby="overview-sources">
          <h2 id="overview-sources" className="mb-2 text-lead font-semibold">
            {t("overview.sources")}
          </h2>
          <DataTable
            columns={columns}
            rows={data.sources}
            rowKey={(s) => s.source_id}
            onActivate={(s) => navigate(`/sources?source=${encodeURIComponent(s.source_id)}`)}
            caption={t("overview.sources")}
          />
        </section>
      ) : null}

      <div className="grid gap-8 lg:grid-cols-2">
        <section aria-labelledby="overview-evidence">
          <h2 id="overview-evidence" className="mb-2 text-lead font-semibold">
            {t("overview.evidence")}
          </h2>
          <ul className="flex flex-col gap-1">
            <li>
              {sealAge === null ? t("overview.noSeal") : t("overview.lastSeal", { age: formatAge(sealAge) })}
            </li>
            {root ? (
              <li className="flex flex-wrap items-center gap-2">
                <span>{t("overview.lastRoot", { window: root.window_id })}</span>
                {root.immudb_verified ? (
                  <span className="rounded-control border border-tier1 px-1.5 text-meta">
                    {t("overview.immudb")}
                  </span>
                ) : null}
              </li>
            ) : null}
            <li>
              <Link to="/evidence" className="hover:underline">
                <StatusDot
                  tone={data.vault.chain_ok ? "good" : "bad"}
                  label={t(data.vault.chain_ok ? "overview.chainIntact" : "overview.chainBroken")}
                />
              </Link>
            </li>
          </ul>
        </section>

        <section aria-labelledby="overview-delivery">
          <h2 id="overview-delivery" className="mb-2 text-lead font-semibold">
            {t("overview.delivery")}
          </h2>
          <ul className="divide-y divide-rule border-y border-rule">
            {data.routes.map((route) => (
              <RouteRow key={route.route_id} route={route} />
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
