import { useState } from "react";
import { Link } from "react-router";
import { useIssueKey, useMe, useRevokeKey, useSourceKeys } from "../../api/queries";
import type { KeyCard } from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Drawer } from "../../components/Drawer";
import { formatEps } from "../../components/format";
import { useToast } from "../../components/Toast";
import { useI18n } from "../../i18n/i18n";
import type { SourceRow } from "./sourceRows";

/** C1's writer roles; everyone else sees the keys but cannot change them. */
const KEY_WRITERS = new Set(["admin", "pack_author"]);

export function SecretOnce({ card }: { card: KeyCard }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(card.secret);
      setCopied(true);
    } catch {
      // Clipboard blocked (permissions, non-secure origin): the secret stays on screen to copy by hand.
    }
  };

  return (
    <div className="mt-3 border border-turmeric bg-highlight/30 p-3">
      <p className="font-medium">{t("sources.drawer.secretOnce")}</p>
      <div className="mt-2 flex items-center gap-2">
        <code className="break-all font-mono text-meta">{card.secret}</code>
        <button
          type="button"
          onClick={() => void copy()}
          className="shrink-0 rounded-control border border-rule px-2 py-1 text-meta"
        >
          {copied ? t("sources.drawer.copied") : t("sources.drawer.copy")}
        </button>
      </div>
      <p className="mt-3 text-meta text-ink-2">{t("sources.drawer.curl")}</p>
      <pre className="mt-1 whitespace-pre-wrap break-all font-mono text-meta">{card.curl_example}</pre>
    </div>
  );
}

function KeysSection({ sourceId, canManage }: { sourceId: string; canManage: boolean }) {
  const { t } = useI18n();
  const push = useToast();
  const keys = useSourceKeys(sourceId);
  const issue = useIssueKey(sourceId);
  const revoke = useRevokeKey(sourceId);
  const [card, setCard] = useState<KeyCard | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const confirmRevoke = () => {
    if (!confirming) return;
    revoke.mutate(confirming, {
      onError: (error) => push(t("common.error", { message: error.message }), "warning"),
      onSettled: () => setConfirming(null),
    });
  };

  return (
    <section aria-labelledby="drawer-keys" className="mt-6">
      <div className="flex items-center justify-between gap-4">
        <h3 id="drawer-keys" className="font-semibold">
          {t("sources.drawer.keys")}
        </h3>
        {canManage ? (
          <button
            type="button"
            disabled={issue.isPending}
            onClick={() => issue.mutate(undefined, { onSuccess: setCard })}
            className="rounded-control bg-thread px-3 py-1.5 text-paper disabled:opacity-60"
          >
            {t("sources.drawer.issueKey")}
          </button>
        ) : null}
      </div>
      {issue.isError ? (
        <p role="alert" className="mt-2">
          {t("common.error", { message: issue.error.message })}
        </p>
      ) : null}
      {card ? <SecretOnce card={card} /> : null}
      {keys.data?.length === 0 ? <p className="mt-2 text-ink-2">{t("sources.drawer.noKeys")}</p> : null}
      {keys.data && keys.data.length > 0 ? (
        <table className="mt-3 w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-rule text-meta text-ink-2">
              <th className="py-1.5 font-medium">{t("sources.drawer.keyId")}</th>
              <th className="py-1.5 font-medium">{t("sources.drawer.keyCreated")}</th>
              <th className="py-1.5 font-medium">{t("sources.col.status")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {keys.data.map((key) => (
              <tr key={key.key_id} className="border-b border-rule">
                <td className="py-1.5">
                  <code className="font-mono text-meta">{key.key_id}</code>
                </td>
                <td className="py-1.5 tabular-nums">{`${key.created_at.slice(0, 16).replace("T", " ")} UTC`}</td>
                <td className="py-1.5">{t(`sources.drawer.keyStatus.${key.status}`)}</td>
                <td className="py-1.5 text-right">
                  {canManage && key.status === "active" ? (
                    <button type="button" onClick={() => setConfirming(key.key_id)} className="text-thread hover:underline">
                      {t("sources.drawer.revoke")}
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <ConfirmDialog
        open={confirming !== null}
        title={t("sources.drawer.revokeTitle")}
        body={t("sources.drawer.revokeConfirm", { key: confirming ?? "" })}
        confirmLabel={t("sources.drawer.revoke")}
        busy={revoke.isPending}
        onConfirm={confirmRevoke}
        onCancel={() => setConfirming(null)}
      />
    </section>
  );
}

export function SourceDrawer({ row, onClose }: { row: SourceRow; onClose: () => void }) {
  const { t } = useI18n();
  const role = useMe().data?.role;
  const { source, health } = row;
  const contractRef = health?.contract_ref ?? source.contract_id;
  const id = encodeURIComponent(source.id);

  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={source.name}
    >
      <section aria-labelledby="drawer-details">
        <h3 id="drawer-details" className="font-semibold">
          {t("sources.drawer.details")}
        </h3>
        <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1">
          <dt className="text-ink-2">{t("sources.col.source")}</dt>
          <dd>
            <code className="font-mono text-meta">{source.id}</code>
          </dd>
          <dt className="text-ink-2">{t("sources.col.tenant")}</dt>
          <dd>
            <code className="font-mono text-meta">{source.tenant_id}</code>
          </dd>
          <dt className="text-ink-2">{t("sources.col.zone")}</dt>
          <dd>{source.zone}</dd>
          <dt className="text-ink-2">{t("sources.col.transport")}</dt>
          <dd>{source.transport}</dd>
          {source.listener ? (
            <>
              <dt className="text-ink-2">{t("sources.drawer.listener")}</dt>
              <dd>
                <code className="font-mono text-meta">{source.listener}</code>
              </dd>
            </>
          ) : null}
          {source.match_kind ? (
            <>
              <dt className="text-ink-2">{t("sources.drawer.match")}</dt>
              <dd>
                <code className="font-mono text-meta">{`${source.match_kind} = ${source.match_value ?? ""}`}</code>
              </dd>
            </>
          ) : null}
          <dt className="text-ink-2">{t("sources.col.contract")}</dt>
          <dd>{contractRef ? <code className="font-mono text-meta">{contractRef}</code> : "—"}</dd>
          <dt className="text-ink-2">{t("sources.col.eps")}</dt>
          <dd className="tabular-nums">{formatEps(source.expected_eps)}</dd>
        </dl>
      </section>

      <KeysSection sourceId={source.id} canManage={role !== undefined && KEY_WRITERS.has(role)} />

      <nav aria-label={t("sources.drawer.links")} className="mt-6 flex flex-col gap-1">
        {source.contract_id ? (
          <Link to={`/contracts/${encodeURIComponent(source.contract_id)}`} className="text-thread hover:underline">
            {t("sources.drawer.history")}
          </Link>
        ) : null}
        <Link to={`/drift?source=${id}`} className="text-thread hover:underline">
          {t("sources.drawer.drift")}
        </Link>
        <Link to={`/lineage?source=${id}`} className="text-thread hover:underline">
          {t("sources.drawer.lineage")}
        </Link>
      </nav>
    </Drawer>
  );
}
