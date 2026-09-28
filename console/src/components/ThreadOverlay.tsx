export interface ThreadOverlayProps {
  from: DOMRect | null;
  to: DOMRect | null;
}

/** A cubic curve between the facing vertical edges of two viewport rectangles. */
export function threadPath(from: DOMRect, to: DOMRect): string {
  const rightward = from.right <= to.left;
  const startX = rightward ? from.right : from.left;
  const endX = rightward ? to.left : to.right;
  const startY = from.top + from.height / 2;
  const endY = to.top + to.height / 2;
  const bend = (endX - startX) / 2;
  return `M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`;
}

export function ThreadOverlay({ from, to }: ThreadOverlayProps) {
  if (!from || !to) return null;
  const d = threadPath(from, to);
  return (
    <svg className="pointer-events-none fixed inset-0 z-50 h-full w-full" aria-hidden="true">
      <path
        key={d}
        className="thread-draw"
        d={d}
        pathLength={1}
        fill="none"
        stroke="var(--color-thread)"
        strokeWidth={1.5}
      />
    </svg>
  );
}
