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
    <div data-stage={id} className="min-w-28 shrink-0 border border-rule bg-paper px-3 py-2">
      <div className="text-meta text-ink-2">{label}</div>
      <div className="text-lead font-medium tabular-nums">{value ?? " "}</div>
    </div>
  );
}

function Line({ eps }: { eps: number }) {
  return (
    <span
      aria-hidden="true"
      className="block w-8 shrink-0 bg-thread"
      style={{ height: `${strokeWidth(eps)}px` }}
    />
  );
}

/** Sources → edge → Kafka, which fans out to the normalizer/router/Wazuh, the vault and the lineage index. */
export function PipelineFlow({ overview }: { overview: Overview }) {
  const { t } = useI18n();
  const eps = overview.eps_1m;
  const epsText = t("overview.eps", { n: formatEps(eps) });
  const deliveredPerMin = overview.routes.reduce((sum, route) => sum + route.delivered_per_min, 0);
  const outEps = deliveredPerMin / 60;

  return (
    <figure aria-label={t("overview.pipeline")} className="overflow-x-auto">
      <div className="flex items-center">
        <Stage id="sources" label={t("overview.sources")} value={String(overview.sources.length)} />
        <Line eps={eps} />
        <Stage id="edge" label={t("overview.edge")} value={epsText} />
        <Line eps={eps} />
        <Stage id="kafka" label={t("overview.kafka")} value={epsText} />
        <div className="flex flex-col gap-3 border-l-2 border-thread">
          <div className="flex items-center">
            <Line eps={eps} />
            <Stage id="normalizer" label={t("overview.normalizer")} value={epsText} />
            <Line eps={outEps} />
            <Stage id="router" label={t("overview.router")} value={t("overview.perMin", { n: deliveredPerMin })} />
            <Line eps={outEps} />
            <Stage id="wazuh" label={t("overview.wazuh")} />
          </div>
          <div className="flex items-center">
            <Line eps={eps} />
            <Stage id="vault" label={t("overview.vault")} value={String(overview.vault.segments)} />
          </div>
          <div className="flex items-center">
            <Line eps={eps} />
            <Stage id="lineage" label={t("overview.lineage")} value={epsText} />
          </div>
        </div>
      </div>
    </figure>
  );
}
