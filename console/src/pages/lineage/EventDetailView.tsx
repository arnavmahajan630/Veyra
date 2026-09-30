import { ArrowLeft } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useEventDetail, useMe } from "../../api/queries";
import { ThreadOverlay } from "../../components/ThreadOverlay";
import { useI18n } from "../../i18n/i18n";
import { DeliveriesList } from "./DeliveriesList";
import { NormalizedPane, type NormalizedPaneHandle } from "./NormalizedPane";
import { RawPane, type ActiveByteSpan } from "./RawPane";
import { RevisionDiff } from "./RevisionDiff";
import { RevisionTimeline } from "./RevisionTimeline";
import { VaultLocation } from "./VaultLocation";
import { VerifyPanel } from "./VerifyPanel";

export interface EventDetailViewProps {
  uid?: string;
  onClose?: () => void;
}

export function EventDetailView({ uid: propUid, onClose }: EventDetailViewProps) {
  const { t } = useI18n();
  const params = useParams();
  const navigate = useNavigate();
  const uid = propUid || params.uid || "";
  const { data: me } = useMe();

  const { data: event, isLoading, error } = useEventDetail(uid);

  const [activeRevNum, setActiveRevNum] = useState<number>(1);
  const [showDiff, setShowDiff] = useState<boolean>(false);

  // Hover and Pin states for Thread linking
  const [hoveredField, setHoveredField] = useState<string | null>(null);
  const [pinnedField, setPinnedField] = useState<string | null>(null);
  const [activeSpan, setActiveSpan] = useState<ActiveByteSpan | null>(null);

  // Element bounds for ThreadOverlay
  const rawPaneRef = useRef<{ getSpanRect(id: string): DOMRect | null }>(null);
  const normPaneRef = useRef<NormalizedPaneHandle>(null);
  const [fromRect, setFromRect] = useState<DOMRect | null>(null);
  const [toRect, setToRect] = useState<DOMRect | null>(null);

  // Set active revision to highest revision on load
  useEffect(() => {
    if (event?.revisions?.length) {
      const highest = Math.max(...event.revisions.map((r) => r.revision));
      setActiveRevNum(highest);
    }
  }, [event]);

  // Update ThreadOverlay bounds on hover/pin
  useEffect(() => {
    const targetKey = pinnedField || hoveredField;
    if (!targetKey) {
      setFromRect(null);
      setToRect(null);
      return;
    }

    const updateRects = () => {
      const rowRect = normPaneRef.current?.getRowRect(targetKey) ?? null;
      const spanRect = rawPaneRef.current?.getSpanRect(targetKey) ?? null;
      setFromRect(rowRect);
      setToRect(spanRect);
    };

    updateRects();
    window.addEventListener("resize", updateRects);
    window.addEventListener("scroll", updateRects, true);
    return () => {
      window.removeEventListener("resize", updateRects);
      window.removeEventListener("scroll", updateRects, true);
    };
  }, [hoveredField, pinnedField, activeSpan]);

  const handleFieldHover = (key: string | null, span?: [number, number]) => {
    if (pinnedField) return; // Keep pinned
    setHoveredField(key);
    if (key && span) {
      setActiveSpan({
        id: key,
        startByte: span[0],
        endByte: span[1],
        tone: "active",
      });
    } else if (!key) {
      setActiveSpan(null);
    }
  };

  const handleFieldPin = (key: string | null, span?: [number, number]) => {
    setPinnedField(key);
    if (key && span) {
      setActiveSpan({
        id: key,
        startByte: span[0],
        endByte: span[1],
        tone: "pinned",
      });
    } else {
      setActiveSpan(null);
    }
  };

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-turmeric border-t-transparent" />
      </div>
    );
  }

  if (error || !event) {
    return (
      <div className="space-y-4 p-6">
        <button
          type="button"
          onClick={() => (onClose ? onClose() : navigate("/lineage"))}
          className="flex items-center gap-1.5 text-meta text-ink-2 hover:text-ink"
        >
          <ArrowLeft className="h-4 w-4" />
          <span>Back to Lineage search</span>
        </button>
        <div className="rounded-md border border-rose-500/20 bg-rose-500/5 p-6 text-center text-rose-500">
          Event {uid} not found.
        </div>
      </div>
    );
  }

  // The bytes come from the vault via the API. When they are missing the raw pane says so
  // — a placeholder line here would put text on screen that this event never contained.
  const rawText = event.raw?.raw_text ?? null;
  const revisions = event.revisions;
  const activeRevision = revisions.find((r) => r.revision === activeRevNum) ?? revisions[0];
  const previousRevision = activeRevision
    ? revisions
        .filter((r) => r.revision < activeRevision.revision)
        .sort((a, b) => b.revision - a.revision)[0]
    : undefined;

  return (
    <div className="space-y-6 p-6">
      {/* Thread connecting normalized row to raw byte span */}
      <ThreadOverlay from={fromRect} to={toRect} />

      {/* Header Strip */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => (onClose ? onClose() : navigate("/lineage"))}
            className="flex items-center gap-1.5 text-meta text-ink-2 hover:text-ink font-medium"
          >
            <ArrowLeft className="h-4 w-4" />
            <span>Search</span>
          </button>
          <span className="text-edge">/</span>
          <h1 className="font-mono text-body font-bold text-ink truncate" title={event.event_uid}>
            Event {event.event_uid}
          </h1>
          {event.raw?.source_id && (
            <span className="rounded bg-surface-2 px-2 py-0.5 text-meta text-ink-2 font-medium">
              {event.raw.source_id}
            </span>
          )}
          {event.raw?.tenant_id && (
            <span className="text-meta text-ink-3">({event.raw.tenant_id})</span>
          )}
        </div>
      </div>

      {event.index_available === false && (
        <p className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-meta text-amber-600">
          {t("lineage.event.indexUnavailable") ||
            "The lineage index is unavailable, so only the sealed raw bytes can be shown. Normalized revisions are not available for this event right now."}
        </p>
      )}

      {/* Revision Timeline */}
      {revisions.length > 0 && (
        <RevisionTimeline
          revisions={revisions}
          activeRevision={activeRevNum}
          onSelectRevision={setActiveRevNum}
          showDiff={showDiff}
          onToggleDiff={setShowDiff}
        />
      )}

      {showDiff && activeRevision && previousRevision && (
        <RevisionDiff before={previousRevision} after={activeRevision} rawText={rawText} />
      )}

      {/* Two-Column Body: Raw Pane (Left) + Normalized Pane (Right) */}
      <div className="grid min-h-[420px] grid-cols-1 gap-4 lg:grid-cols-2">
        <RawPane
          ref={rawPaneRef}
          text={rawText ?? ""}
          byteLength={event.raw?.raw_len}
          sha256={event.raw?.raw_sha256}
          activeSpan={activeSpan}
        />
        {activeRevision ? (
          <NormalizedPane
            ref={normPaneRef}
            revision={activeRevision}
            rawText={rawText}
            activeKey={hoveredField}
            pinnedKey={pinnedField}
            onFieldHover={handleFieldHover}
            onFieldPin={handleFieldPin}
          />
        ) : (
          <div className="flex items-center justify-center rounded-md border border-edge bg-surface-1 p-6 text-center text-meta text-ink-3">
            {t("lineage.event.noRevisions") ||
              "No normalized revision has been indexed for this event yet."}
          </div>
        )}
      </div>

      {/* Verify Panel */}
      <VerifyPanel eventUid={event.event_uid} demoMode={me?.demo_mode} />

      {/* Footer Lists: Deliveries & Vault Location */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <DeliveriesList receipts={event.receipts} currentRevision={activeRevNum} />
        <VaultLocation vault={event.vault} />
      </div>
    </div>
  );
}
