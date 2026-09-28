import { useDemoSwitch, useLogout, useTenants } from "../api/queries";
import type { Me } from "../api/types";
import { StatusDot, type StatusTone } from "../components/StatusDot";
import { useI18n, type Lang } from "../i18n/i18n";
import { useConnectionState, type SseState } from "../sse/connection";
import { useTenantScope } from "./tenant";

/** Four-eyes beat (04_DEMO_SCRIPT): one click swaps author and approver (C5 AC5). */
const DEMO_PARTNER: Record<string, string> = {
  "author@maha": "approver@veyra",
  "approver@veyra": "author@maha",
  "admin@veyra": "author@maha",
};

const CONNECTION: Record<SseState, { tone: StatusTone; key: string }> = {
  open: { tone: "good", key: "header.live" },
  connecting: { tone: "idle", key: "header.connecting" },
  retrying: { tone: "warn", key: "header.reconnecting" },
  idle: { tone: "idle", key: "header.idle" },
};

/** Language names are written in their own script, so they are not translated. */
const LANGUAGES: Record<Lang, string> = { en: "English", hi: "हिन्दी" };

const SELECT = "rounded-control border border-rule bg-paper px-2 py-1 text-body text-ink";

export function Header({ me }: { me: Me }) {
  const { t, lang, setLang } = useI18n();
  const { scope, canChoose, choose } = useTenantScope();
  const tenants = useTenants(canChoose);
  const connection = CONNECTION[useConnectionState()];
  const logout = useLogout();
  const demoSwitch = useDemoSwitch();
  const partner = me.demo_mode ? DEMO_PARTNER[me.user.email] : undefined;

  return (
    <header className="flex h-14 shrink-0 items-center gap-6 border-b border-rule px-6">
      <span className="text-lead font-semibold">VEYRA</span>
      {canChoose ? (
        <label className="flex items-center gap-2 text-meta text-ink-2">
          {t("header.tenant")}
          <select value={scope ?? ""} onChange={(event) => choose(event.target.value || null)} className={SELECT}>
            <option value="">{t("header.allTenants")}</option>
            {tenants.data?.map((tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.name}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <code className="font-mono text-meta text-ink-2">{me.tenant}</code>
      )}
      <div className="ml-auto flex items-center gap-5">
        <StatusDot tone={connection.tone} label={t(connection.key)} />
        <label className="flex items-center gap-2 text-meta text-ink-2">
          {t("header.language")}
          <select value={lang} onChange={(event) => setLang(event.target.value as Lang)} className={SELECT}>
            {Object.entries(LANGUAGES).map(([code, name]) => (
              <option key={code} value={code}>
                {name}
              </option>
            ))}
          </select>
        </label>
        {partner ? (
          <button
            type="button"
            disabled={demoSwitch.isPending}
            onClick={() => demoSwitch.mutate(partner)}
            className="rounded-control border border-thread px-3 py-1 text-thread disabled:opacity-60"
          >
            {t("header.switchUser", { email: partner })}
          </button>
        ) : null}
        <span className="flex flex-col text-right text-meta leading-tight">
          <span className="font-medium text-ink">{me.user.email}</span>
          <span className="text-ink-2">{t(`role.${me.role}`)}</span>
        </span>
        <button type="button" onClick={() => logout.mutate()} className="text-meta text-thread hover:underline">
          {t("header.signOut")}
        </button>
      </div>
    </header>
  );
}
