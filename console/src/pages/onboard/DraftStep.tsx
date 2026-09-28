import { useDraft, useSubmitDraft } from "../../api/queries";
import type { VersionDetail } from "../../api/types";
import { DraftReview } from "../../components/review/DraftReview";
import { firstFailure } from "../../components/review/mappings";
import { useI18n } from "../../i18n/i18n";

/** Step 4: one compact review per template, then Create contract (submit the draft). */
export function DraftStep({ draftId, onSubmitted }: { draftId: string; onSubmitted: (version: VersionDetail) => void }) {
  const { t } = useI18n();
  const draft = useDraft(draftId);
  const submit = useSubmitDraft(draftId);
  if (!draft.data) {
    return draft.error ? <p role="alert">{draft.error.message}</p> : <p className="text-ink-2">{t("common.loading")}</p>;
  }
  const failure = firstFailure(draft.data.verification);
  return (
    <section aria-labelledby="onboard-draft">
      <h2 id="onboard-draft" className="text-lead font-semibold">
        {t("onboard.draftStep")}
      </h2>
      <div className="mt-3 grid gap-6 xl:grid-cols-2">
        {draft.data.templates.map((template, index) => (
          <DraftReview key={template.template_sig} draft={draft.data} mode="compact" templateIndex={index} />
        ))}
      </div>
      {draft.data.state === "ready" ? (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={failure !== null || submit.isPending}
            onClick={() => submit.mutate(undefined, { onSuccess: onSubmitted })}
            className="rounded-control bg-thread px-3 py-1.5 text-paper disabled:opacity-50"
          >
            {t("onboard.createContract")}
          </button>
          {failure ? <p className="text-meta">{failure}</p> : null}
          {submit.error ? <p role="alert">{submit.error.message}</p> : null}
        </div>
      ) : null}
    </section>
  );
}
