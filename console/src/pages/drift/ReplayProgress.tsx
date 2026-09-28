import { Link } from "react-router";
import { useReplay } from "../../api/queries";
import { useI18n } from "../../i18n/i18n";

/** A replay job's progress; `useReplay` polls until it is final, so a missed SSE `done` can't strand it. */
export function ReplayProgress({ jobId, sig }: { jobId: string; sig: string }) {
  const { t } = useI18n();
  const job = useReplay(jobId).data;
  if (!job) return <p className="text-ink-2">{t("common.loading")}</p>;
  if (job.state === "done") {
    return (
      <Link to={`/lineage?q=${encodeURIComponent(sig)}`} className="text-thread hover:underline">
        {t("drift.replayed", { n: job.normalized })}
      </Link>
    );
  }
  if (job.state === "failed" || job.state === "timed_out") {
    return <p role="alert">{t("drift.replayStopped", { state: job.state, detail: job.detail })}</p>;
  }
  const total = Math.max(job.total, 1);
  return (
    <div className="flex w-full max-w-md flex-col gap-1">
      <div
        role="progressbar"
        aria-label={t("drift.replaying", { done: job.normalized, total: job.total })}
        aria-valuemin={0}
        aria-valuemax={job.total}
        aria-valuenow={job.normalized}
        className="h-2 w-full bg-rule/50"
      >
        <div className="h-full bg-thread" style={{ width: `${(100 * job.normalized) / total}%` }} />
      </div>
      <p className="text-meta tabular-nums">{t("drift.replaying", { done: job.normalized, total: job.total })}</p>
    </div>
  );
}
