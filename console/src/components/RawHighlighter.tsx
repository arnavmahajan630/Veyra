import { Fragment, useImperativeHandle, useRef, type Ref } from "react";

export type SpanTone = "active" | "pinned" | "muted";

export interface HighlightSpan {
  id: string;
  startChar: number;
  endChar: number;
  tone: SpanTone;
}

export interface RawHighlighterHandle {
  getSpanRect(id: string): DOMRect | null;
}

export interface RawHighlighterProps {
  text: string;
  spans: readonly HighlightSpan[];
  activeId?: string;
  onSpanHover?: (id: string | null) => void;
  ref?: Ref<RawHighlighterHandle>;
}

export const VIRTUALIZE_AFTER_LINES = 500;
export const LINES_PER_BLOCK = 100;
const LINE_HEIGHT_PX = 18;

const TONE_CLASS: Record<SpanTone, string> = {
  active: "bg-highlight outline outline-1 outline-turmeric",
  pinned: "bg-highlight",
  muted: "bg-highlight/40",
};

interface Segment {
  from: number;
  to: number;
  span?: HighlightSpan;
}

/** Split [from, to) into plain and highlighted runs; a span overlapping an earlier one is skipped. */
function segment(spans: readonly HighlightSpan[], from: number, to: number): Segment[] {
  const inRange = spans
    .filter((s) => s.endChar > s.startChar && s.endChar > from && s.startChar < to)
    .sort((a, b) => a.startChar - b.startChar);
  const out: Segment[] = [];
  let cursor = from;
  for (const span of inRange) {
    const start = Math.max(span.startChar, from);
    const end = Math.min(span.endChar, to);
    if (start < cursor) continue;
    if (start > cursor) out.push({ from: cursor, to: start });
    out.push({ from: start, to: end, span });
    cursor = end;
  }
  if (cursor < to) out.push({ from: cursor, to });
  return out;
}

/** Character ranges to render: one block, or blocks of LINES_PER_BLOCK lines for long text. */
function blocks(text: string): Array<[number, number]> {
  const lineStarts = [0];
  for (let i = 0; i < text.length; i += 1) if (text[i] === "\n") lineStarts.push(i + 1);
  if (lineStarts.length <= VIRTUALIZE_AFTER_LINES) return [[0, text.length]];
  const out: Array<[number, number]> = [];
  for (let line = 0; line < lineStarts.length; line += LINES_PER_BLOCK) {
    out.push([lineStarts[line] as number, lineStarts[line + LINES_PER_BLOCK] ?? text.length]);
  }
  return out;
}

export function RawHighlighter({ text, spans, activeId, onSpanHover, ref }: RawHighlighterProps) {
  const root = useRef<HTMLPreElement>(null);

  useImperativeHandle(
    ref,
    () => ({
      getSpanRect(id: string) {
        const marks = root.current?.querySelectorAll<HTMLElement>("mark[data-span-id]") ?? [];
        for (const mark of marks) if (mark.dataset.spanId === id) return mark.getBoundingClientRect();
        return null;
      },
    }),
    [],
  );

  const ranges = blocks(text);
  const virtualized = ranges.length > 1;

  const renderRange = (from: number, to: number) =>
    segment(spans, from, to).map((piece) => {
      const span = piece.span;
      if (!span) return <span key={piece.from}>{text.slice(piece.from, piece.to)}</span>;
      const tone: SpanTone = span.id === activeId ? "active" : span.tone;
      return (
        <mark
          key={piece.from}
          data-span-id={span.id}
          data-tone={tone}
          className={`rounded-[2px] text-ink ${TONE_CLASS[tone]}`}
          onMouseEnter={() => onSpanHover?.(span.id)}
        >
          {text.slice(piece.from, piece.to)}
        </mark>
      );
    });

  return (
    <pre
      ref={root}
      className="whitespace-pre-wrap break-all font-mono text-meta text-ink"
      onMouseLeave={() => onSpanHover?.(null)}
    >
      {ranges.map(([from, to]) =>
        virtualized ? (
          <span
            key={from}
            data-block=""
            style={{
              display: "block",
              contentVisibility: "auto",
              containIntrinsicSize: `auto ${LINES_PER_BLOCK * LINE_HEIGHT_PX}px`,
            }}
          >
            {renderRange(from, to)}
          </span>
        ) : (
          <Fragment key={from}>{renderRange(from, to)}</Fragment>
        ),
      )}
    </pre>
  );
}
