import { useImperativeHandle, useMemo, useRef, type Ref } from "react";
import {
  RawHighlighter,
  type HighlightSpan,
  type RawHighlighterHandle,
} from "../../components/RawHighlighter";
import { byteSpanToChar } from "./byteToChar";

export interface ActiveByteSpan {
  id: string;
  startByte: number;
  endByte: number;
  tone: "active" | "pinned" | "muted";
}

export interface RawPaneProps {
  text: string;
  byteLength?: number;
  sha256?: string;
  activeSpan?: ActiveByteSpan | null;
  onSpanHover?: (id: string | null) => void;
  ref?: Ref<RawHighlighterHandle>;
}

export function RawPane({
  text,
  byteLength,
  sha256,
  activeSpan,
  onSpanHover,
  ref,
}: RawPaneProps) {
  const highlighterRef = useRef<RawHighlighterHandle>(null);

  useImperativeHandle(ref, () => ({
    getSpanRect(id: string) {
      return highlighterRef.current?.getSpanRect(id) ?? null;
    },
  }));

  const spans: readonly HighlightSpan[] = useMemo(() => {
    if (!activeSpan || activeSpan.endByte <= activeSpan.startByte) return [];
    const [startChar, endChar] = byteSpanToChar(text, [
      activeSpan.startByte,
      activeSpan.endByte,
    ]);
    return [
      {
        id: activeSpan.id,
        startChar,
        endChar,
        tone: activeSpan.tone,
      },
    ];
  }, [text, activeSpan]);

  return (
    <div className="flex h-full flex-col rounded-md border border-edge bg-surface-1">
      <div className="flex items-center justify-between border-b border-edge bg-surface-2 px-3 py-2 text-meta font-medium text-ink-2">
        <span className="font-semibold uppercase tracking-wider text-micro text-ink">RAW</span>
        <span className="font-mono text-micro text-ink-3">exact bytes</span>
      </div>

      <div className="flex-1 overflow-auto p-3 font-mono text-body">
        <RawHighlighter
          ref={highlighterRef}
          text={text}
          spans={spans}
          activeId={activeSpan?.id}
          onSpanHover={onSpanHover}
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-edge bg-surface-2 px-3 py-1.5 font-mono text-micro text-ink-3">
        <div className="flex items-center gap-2">
          <span>sha256</span>
          <span className="text-ink-2 font-medium" title={sha256}>
            {sha256 ? `${sha256.slice(0, 8)}...${sha256.slice(-8)}` : "—"}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span>{byteLength ?? new TextEncoder().encode(text).length} bytes</span>
          <span className="rounded bg-surface-3 px-1 py-0.5 text-micro text-ink-2">utf-8</span>
        </div>
      </div>
    </div>
  );
}
