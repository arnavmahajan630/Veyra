import { useState, type FormEvent } from "react";
import { useCreateSource, useMe, useTenants } from "../../api/queries";
import { PLATFORM, type SourceTransport } from "../../api/types";
import { useI18n } from "../../i18n/i18n";
import { slug } from "./layers";

const FIELD = "rounded-control border border-rule bg-paper px-2 py-1 text-body";
const LABEL = "flex flex-col gap-1 text-meta text-ink-2";
const ZONES = ["core", "dmz", "ot", "external"] as const;
/** control-api's syslog listeners (keys.py LISTENER_PORTS). */
const LISTENERS = ["core-udp", "core-tcp", "dmz-udp", "dmz-tcp"];

export interface SourceStepProps {
  sourceId: string | null;
  onCreated: (sourceId: string) => void;
}

/** Step 1: create the source the samples come from (onboarding analyzes an existing source). */
export function SourceStep({ sourceId, onCreated }: SourceStepProps) {
  const { t } = useI18n();
  const me = useMe().data;
  const platform = me?.tenant === PLATFORM;
  const tenants = useTenants(platform);
  const create = useCreateSource();
  const [name, setName] = useState("");
  const [tenant, setTenant] = useState("");
  const [transport, setTransport] = useState<SourceTransport>("http_push");
  const [zone, setZone] = useState<(typeof ZONES)[number]>("core");
  const [eps, setEps] = useState("10");
  const [listener, setListener] = useState("");
  const [host, setHost] = useState("");

  if (sourceId) {
    return (
      <section aria-labelledby="onboard-source">
        <h2 id="onboard-source" className="text-lead font-semibold">
          {t("onboard.sourceStep")}
        </h2>
        <p className="mt-1">{t("onboard.sourceCreated", { id: sourceId })}</p>
      </section>
    );
  }

  const syslog = transport !== "http_push";
  const tenantId = platform ? tenant || tenants.data?.[0]?.id || "" : (me?.tenant ?? "");
  const base = slug(name);
  const defaultListener = `${zone === "dmz" ? "dmz" : "core"}-${transport === "syslog_tcp" ? "tcp" : "udp"}`;
  const ready = base !== "" && tenantId !== "" && (!syslog || host.trim() !== "");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    const id = `src_${base}_01`;
    create.mutate(
      {
        id,
        tenant_id: tenantId,
        name: name.trim(),
        vendor: /^[a-z]/.test(base) ? base : `v_${base}`,
        zone,
        transport,
        expected_eps: Number(eps) || 0,
        ...(syslog ? { listener: listener || defaultListener, match_kind: "syslog_host", match_value: host.trim() } : {}),
      },
      { onSuccess: () => onCreated(id) },
    );
  };

  return (
    <section aria-labelledby="onboard-source">
      <h2 id="onboard-source" className="text-lead font-semibold">
        {t("onboard.sourceStep")}
      </h2>
      <form onSubmit={submit} className="mt-3 grid max-w-3xl gap-4 sm:grid-cols-2">
        <label className={LABEL}>
          {t("onboard.name")}
          <input value={name} onChange={(e) => setName(e.target.value)} className={FIELD} />
        </label>
        {platform ? (
          <label className={LABEL}>
            {t("onboard.tenant")}
            <select value={tenantId} onChange={(e) => setTenant(e.target.value)} className={FIELD}>
              {tenants.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <label className={LABEL}>
          {t("onboard.transport")}
          <select value={transport} onChange={(e) => setTransport(e.target.value as SourceTransport)} className={FIELD}>
            <option value="http_push">http_push</option>
            <option value="syslog_udp">syslog_udp</option>
            <option value="syslog_tcp">syslog_tcp</option>
          </select>
        </label>
        <label className={LABEL}>
          {t("onboard.zone")}
          <select value={zone} onChange={(e) => setZone(e.target.value as (typeof ZONES)[number])} className={FIELD}>
            {ZONES.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </label>
        <label className={LABEL}>
          {t("onboard.expectedEps")}
          <input type="number" min="0" value={eps} onChange={(e) => setEps(e.target.value)} className={FIELD} />
        </label>
        {syslog ? (
          <>
            <label className={LABEL}>
              {t("onboard.listener")}
              <select value={listener || defaultListener} onChange={(e) => setListener(e.target.value)} className={FIELD}>
                {LISTENERS.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            <label className={LABEL}>
              {t("onboard.host")}
              <input value={host} onChange={(e) => setHost(e.target.value)} className={`${FIELD} font-mono`} />
            </label>
          </>
        ) : null}
        <div className="flex items-center gap-3 sm:col-span-2">
          <button
            type="submit"
            disabled={!ready || create.isPending}
            className="rounded-control bg-thread px-3 py-1.5 text-paper disabled:opacity-50"
          >
            {t("onboard.createSource")}
          </button>
          {create.error ? (
            <p role="alert" className="text-meta">
              {create.error.message}
            </p>
          ) : null}
        </div>
      </form>
    </section>
  );
}
