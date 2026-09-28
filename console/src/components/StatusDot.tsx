export type StatusTone = "good" | "warn" | "bad" | "idle";

const TONE_BG: Record<StatusTone, string> = {
  good: "bg-tier1",
  warn: "bg-tier2",
  bad: "bg-tier4",
  idle: "bg-rule",
};

export function StatusDot({ tone, label }: { tone: StatusTone; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-meta text-ink-2" title={label}>
      <span aria-hidden="true" className={`inline-block h-2 w-2 rounded-full ${TONE_BG[tone]}`} />
      <span>{label}</span>
    </span>
  );
}
