import { Copy, Download, KeyRound } from "lucide-react";
import { useEffect, useState } from "react";
import { useEvidencePubkey } from "../../api/queries";
import { useI18n } from "../../i18n/i18n";

/** The DER bytes inside a PEM block, i.e. the base64 body with the armour stripped. */
export function derFromPem(pem: string): Uint8Array {
  const body = pem
    .split("\n")
    .filter((line) => !line.startsWith("-----"))
    .join("")
    .trim();
  const binary = atob(body);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

/** SHA-256 of the DER, grouped in pairs — what an auditor reads out loud to compare keys. */
export async function fingerprintOf(pem: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", derFromPem(pem) as BufferSource);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join(":");
}

export function PublicKeyPanel() {
  const { t } = useI18n();
  const { data: pem, isLoading, isError } = useEvidencePubkey();
  const [copied, setCopied] = useState(false);
  const [fingerprint, setFingerprint] = useState<string | null>(null);

  useEffect(() => {
    if (!pem) return;
    let live = true;
    fingerprintOf(pem)
      .then((value) => {
        if (live) setFingerprint(value);
      })
      .catch(() => {
        if (live) setFingerprint(null);
      });
    return () => {
      live = false;
    };
  }, [pem]);

  const handleCopy = () => {
    if (!pem) return;
    navigator.clipboard.writeText(pem);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownload = () => {
    if (!pem) return;
    const blob = new Blob([pem], { type: "application/x-pem-file" });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "veyra-evidence-signing-public.pem";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  };

  return (
    <div className="rounded-md border border-edge bg-surface-1 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-turmeric" />
          <h3 className="text-meta font-semibold text-ink">
            {t("evidence.pubkey.title") || "Window Signing Public Key (Ed25519)"}
          </h3>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleCopy}
            disabled={!pem || isLoading}
            className="flex items-center gap-1.5 rounded border border-edge bg-surface-2 px-2.5 py-1 text-micro font-medium text-ink-2 hover:bg-surface-3 transition-colors disabled:opacity-50"
          >
            <Copy className="h-3 w-3" />
            <span>{copied ? t("common.copied") || "Copied" : t("evidence.pubkey.copy") || "Copy PEM"}</span>
          </button>
          <button
            type="button"
            onClick={handleDownload}
            disabled={!pem || isLoading}
            className="flex items-center gap-1.5 rounded border border-edge bg-surface-2 px-2.5 py-1 text-micro font-medium text-ink-2 hover:bg-surface-3 transition-colors disabled:opacity-50"
          >
            <Download className="h-3 w-3" />
            <span>{t("evidence.pubkey.download") || "Download .pem"}</span>
          </button>
        </div>
      </div>

      {fingerprint && (
        <p className="font-mono text-micro text-ink-2">
          <span className="text-ink-3">
            {t("evidence.pubkey.fingerprint") || "SHA-256 of the DER"}:{" "}
          </span>
          <span data-testid="pubkey-fingerprint">{fingerprint}</span>
        </p>
      )}

      <div className="overflow-x-auto rounded border border-edge bg-surface-2/40 p-3 font-mono text-micro text-ink-2">
        {isLoading ? (
          <p className="text-ink-3">{t("evidence.pubkey.loading") || "Loading public key…"}</p>
        ) : isError || !pem ? (
          <p className="text-rose-600">
            {t("evidence.pubkey.failed") || "The signing public key could not be fetched."}
          </p>
        ) : (
          <pre className="whitespace-pre-wrap">{pem.trim()}</pre>
        )}
      </div>
    </div>
  );
}
