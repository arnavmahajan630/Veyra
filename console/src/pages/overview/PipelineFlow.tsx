import type { Overview } from "../../api/types";
import { formatEps } from "../../components/format";
import { useI18n } from "../../i18n/i18n";

/** Line thickness grows with throughput on a log scale, 1 px idle to 6 px. The line never moves. */
export function strokeWidth(eps: number): number {
  const width = 1 + Math.min(5, 2 * Math.log10(1 + Math.max(0, eps)));
  return Math.round(width * 10) / 10;
}

function Stage({ id, label, value }: { id: string; label: string; value?: string }) {
  return (
    <div data-stage={id} className="my-1.5 min-w-26 border border-rule bg-paper px-3 py-2">
      <div className="whitespace-nowrap text-meta text-ink-2">{label}</div>
      <div className="whitespace-nowrap text-lead font-medium tabular-nums">{value ?? "—"}</div>
    </div>
  );
}

/** A straight connector between two stages on the same row. */
function Line({ eps }: { eps: number }) {
  return (
    <span aria-hidden="true" className="block w-6 self-center bg-thread" style={{ height: `${strokeWidth(eps)}px` }} />
  );
}

/**
 * The branch after Kafka (C5: `Kafka ─┬─ Normalizer / ├─ Vault / └─ Lineage index`): each row's
 * cell draws its piece of the spine, so the three pieces join into one line.
 */
function Branch({ eps, position }: { eps: number; position: "first" | "middle" | "last" }) {
  const spine = { first: "top-1/2 bottom-0", middle: "inset-y-0", last: "top-0 bottom-1/2" }[position];
  return (
    <span aria-hidden="true" className="relative block w-6 self-stretch">
      <span className={`absolute left-0 w-0.5 bg-thread ${spine}`} />
      <span
        className={`absolute top-1/2 -translate-y-1/2 bg-thread ${position === "first" ? "-left-px right-0" : "left-0 right-0"}`}
        style={{ height: `${strokeWidth(eps)}px` }}
      />
    </span>
  );
}

/** Sources → edge → Kafka → normalizer → router → Wazuh on one line; Kafka also feeds the vault and the lineage index. */
export function PipelineFlow({ overview }: { overview: Overview }) {
  const { t } = useI18n();
  const eps = overview.eps_1m;
  const epsText = t("overview.eps", { n: formatEps(eps) });
  const deliveredPerMin = overview.routes.reduce((sum, route) => sum + route.delivered_per_min, 0);
  const outEps = deliveredPerMin / 60;
  const wazuh = overview.routes.find((route) => route.route_id.startsWith("wazuh"));
  const wazuhLag = wazuh?.lag_s == null ? undefined : t("overview.lag", { n: wazuh.lag_s });

  return (
    <figure aria-label={t("overview.pipeline")} className="overflow-x-auto">
      <div className="grid w-max grid-cols-[repeat(11,auto)]">
        <Stage id="sources" label={t("overview.sources")} value={String(overview.sources.length)} />
        <Line eps={eps} />
        <Stage id="edge" label={t("overview.edge")} value={epsText} />
        <Line eps={eps} />
        <Stage id="kafka" label={t("overview.kafka")} value={epsText} />
        <Branch eps={eps} position="first" />
        <Stage id="normalizer" label={t("overview.normalizer")} value={epsText} />
        <Line eps={outEps} />
        <Stage id="router" label={t("overview.router")} value={t("overview.perMin", { n: deliveredPerMin })} />
        <Line eps={outEps} />
        <Stage id="wazuh" label={t("overview.wazuh")} value={wazuhLag} />

        <span className="col-span-5" />
        <Branch eps={eps} position="middle" />
        <Stage id="vault" label={t("overview.vault")} value={t("overview.segments", { n: overview.vault.segments })} />
        <span className="col-span-4" />

        <span className="col-span-5" />
        <Branch eps={eps} position="last" />
        <Stage id="lineage" label={t("overview.lineage")} value={epsText} />
        <span className="col-span-4" />
      </div>
    </figure>
  );
}
