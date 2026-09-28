import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { useContract, useContractVersion, useDiff } from "../../api/queries";
import type { ContractDetail } from "../../api/types";
import { BacktestStrip } from "../../components/review/BacktestStrip";
import { YamlView } from "../../components/review/YamlView";
import { useI18n } from "../../i18n/i18n";
import { ContractStateDot } from "./ContractStateDot";
import { DiffView } from "./DiffView";
import { LifecycleActions } from "./LifecycleActions";
import { VersionTimeline } from "./VersionTimeline";

const TABS = ["yaml", "golden", "backtest", "diff"] as const;
type Tab = (typeof TABS)[number];

/** The version to show first: the canary under review, else the active one, else the latest. */
const defaultVersion = (c: ContractDetail) => c.canary_version ?? c.active_version ?? c.latest_version;

/** One contract: header, lifecycle history, a version picker and YAML | Golden | Backtest | Diff (C6). */
export default function ContractDetailPage() {
  const { t } = useI18n();
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const contract = useContract(id);
  const [tab, setTab] = useState<Tab>("yaml");
  const picked = Number(params.get("v")) || null;
  // Pin the version the page opened on: an approve, promote or rollback changes which version is
  // the default, and the page must not jump away from the one being acted on.
  const [opened, setOpened] = useState<number | null>(null);
  useEffect(() => {
    if (opened === null && contract.data) setOpened(defaultVersion(contract.data));
  }, [opened, contract.data]);
  const v = picked ?? opened ?? (contract.data ? defaultVersion(contract.data) : null);
  const version = useContractVersion(id, v);
  const diff = useDiff(id, (v ?? 1) - 1, v ?? 1, tab === "diff" && (v ?? 1) > 1);

  if (contract.isPending) return <p className="p-6 text-ink-2">{t("common.loading")}</p>;
  if (contract.isError) {
    return (
      <p role="alert" className="p-6">
        {t("common.error", { message: contract.error.message })}
      </p>
    );
  }
  const c = contract.data;
  const row = version.data;

  return (
    <div className="flex max-w-6xl flex-col gap-6 p-6">
      <header className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <h1 className="font-mono text-title font-semibold">{c.id}</h1>
        <code className="font-mono text-meta text-ink-2">{c.tenant_id}</code>
        <span>
          {t("contracts.col.active")}: {c.active_version === null ? "—" : `v${c.active_version}`}
        </span>
        <span>
          {t("contracts.col.canary")}: {c.canary_version === null ? "—" : `v${c.canary_version}`}
        </span>
      </header>

      <div className="grid gap-8 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <VersionTimeline history={c.history} />

        <div className="flex min-w-0 flex-col gap-4">
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-meta text-ink-2">
              {t("contracts.versionLabel")}
              <select
                value={v ?? ""}
                onChange={(e) => setParams({ v: e.target.value })}
                className="rounded-control border border-rule bg-paper px-2 py-1 text-body text-ink"
              >
                {[...c.versions].reverse().map((x) => (
                  <option key={x.version} value={x.version}>
                    {t("contracts.versionOption", { v: x.version, state: t(`contracts.state.${x.state}`) })}
                  </option>
                ))}
              </select>
            </label>
            {row ? <ContractStateDot state={row.state} /> : null}
          </div>

          {row ? <LifecycleActions contract={c} version={row} /> : null}

          <div role="tablist" aria-label={t("contracts.versionLabel")} className="flex gap-1 border-b border-rule">
            {TABS.map((name) => (
              <button
                key={name}
                type="button"
                role="tab"
                aria-selected={tab === name}
                onClick={() => setTab(name)}
                className={`-mb-px border-b-2 px-3 py-1.5 ${tab === name ? "border-thread text-ink" : "border-transparent text-ink-2"}`}
              >
                {t(`contracts.tab.${name}`)}
              </button>
            ))}
          </div>

          <div role="tabpanel">
            {!row ? (
              <p className="text-ink-2">{t("common.loading")}</p>
            ) : tab === "yaml" ? (
              <YamlView yaml={row.yaml} />
            ) : tab === "golden" ? (
              row.golden ? (
                <p>{t("contracts.goldenSummary", { passed: row.golden.total - row.golden.failed, total: row.golden.total })}</p>
              ) : (
                <p className="text-ink-2">{t("contracts.noGolden")}</p>
              )
            ) : tab === "backtest" ? (
              row.backtest ? (
                <BacktestStrip backtest={row.backtest} sig={row.backtest.sigs[0] ?? ""} />
              ) : (
                <p className="text-ink-2">{t("contracts.noBacktest")}</p>
              )
            ) : row.version === 1 ? (
              <p className="text-ink-2">{t("contracts.firstVersion")}</p>
            ) : diff.data ? (
              <DiffView diff={diff.data} />
            ) : (
              <p className="text-ink-2">{t("common.loading")}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
