import { useId, useState } from "react";
import { useMe } from "../../api/queries";
import { useI18n } from "../../i18n/i18n";
import { DEMO_SAMPLES } from "./demoSamples";

/** Step 2: the pasted samples, sent byte for byte (control-api splits them; Review Focus 4). */
export function SamplesStep({ onAnalyze, busy }: { onAnalyze: (samples: string) => void; busy: boolean }) {
  const { t } = useI18n();
  const demo = useMe().data?.demo_mode ?? false;
  const [text, setText] = useState("");
  const id = useId();
  return (
    <section aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className="text-lead font-semibold">
        <label htmlFor={id}>{t("onboard.samples")}</label>
      </h2>
      <textarea
        id={id}
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={8}
        spellCheck={false}
        className="mt-2 w-full max-w-5xl rounded-control border border-rule bg-paper p-2 font-mono text-meta"
      />
      <div className="mt-2 flex gap-3">
        {demo ? (
          <button type="button" onClick={() => setText(DEMO_SAMPLES)} className="rounded-control border border-rule px-3 py-1.5">
            {t("onboard.pasteSamples")}
          </button>
        ) : null}
        <button
          type="button"
          disabled={text.trim() === "" || busy}
          onClick={() => onAnalyze(text)}
          className="rounded-control bg-thread px-3 py-1.5 text-paper disabled:opacity-50"
        >
          {t("onboard.analyze")}
        </button>
      </div>
    </section>
  );
}
