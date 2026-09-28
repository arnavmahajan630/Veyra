import { useState } from "react";
import { useIssueKey, useSources } from "../../api/queries";
import type { KeyCard } from "../../api/types";
import { useI18n } from "../../i18n/i18n";
import { SecretOnce } from "../sources/SourceDrawer";

/** Step 6: HTTP push sources get a key (the secret shown once); syslog sources get their target. */
export function KeyStep({ sourceId }: { sourceId: string }) {
  const { t } = useI18n();
  const source = useSources(null).data?.find((s) => s.id === sourceId);
  const issue = useIssueKey(sourceId);
  const [card, setCard] = useState<KeyCard | null>(null);
  const [saved, setSaved] = useState(false);
  const syslog = source !== undefined && source.transport !== "http_push";

  return (
    <section aria-labelledby="onboard-key">
      <h2 id="onboard-key" className="text-lead font-semibold">
        {t("onboard.keyStep")}
      </h2>
      {syslog ? (
        <p className="mt-1">
          {t("onboard.syslogTarget", { host: source.match_value ?? "", listener: source.listener ?? "" })}
        </p>
      ) : card === null ? (
        <div className="mt-2 flex items-center gap-3">
          <button
            type="button"
            disabled={issue.isPending}
            onClick={() => issue.mutate(undefined, { onSuccess: setCard })}
            className="rounded-control bg-thread px-3 py-1.5 text-paper disabled:opacity-50"
          >
            {t("onboard.issueKey")}
          </button>
          {issue.error ? <p role="alert">{issue.error.message}</p> : null}
        </div>
      ) : saved ? (
        <p className="mt-1">{t("onboard.secretHidden", { id: card.key_id })}</p>
      ) : (
        <div className="max-w-3xl">
          <SecretOnce card={card} />
          <button type="button" onClick={() => setSaved(true)} className="mt-2 rounded-control border border-rule px-3 py-1.5">
            {t("onboard.saved")}
          </button>
        </div>
      )}
    </section>
  );
}
