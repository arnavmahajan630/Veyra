import { useRef, useState } from "react";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { DataTable } from "../../components/DataTable";
import { Drawer } from "../../components/Drawer";
import { Kbd } from "../../components/Kbd";
import { MixBar } from "../../components/MixBar";
import { RawHighlighter, type RawHighlighterHandle } from "../../components/RawHighlighter";
import { StatusDot } from "../../components/StatusDot";
import { ThreadOverlay } from "../../components/ThreadOverlay";
import { TierBar } from "../../components/TierBar";
import { TierChip } from "../../components/TierChip";
import { useToast } from "../../components/Toast";
import { byteSpanToChar } from "../../components/byteSpan";
import { TIERS } from "../../components/tiers";

export interface Sample {
  id: string;
  title: string;
  text: string;
  /** [normalized field, the exact raw text it came from] */
  fields: ReadonlyArray<readonly [string, string]>;
}

export const SAMPLES: readonly Sample[] = [
  {
    id: "ascii",
    title: "ASCII syslog",
    text: "<38>Sep 26 14:05:11 core-lnx-07 sshd[233]: Failed password for a.sharma from 103.21.4.77 port 52211 ssh2",
    fields: [
      ["user.name", "a.sharma"],
      ["src_endpoint.ip", "103.21.4.77"],
    ],
  },
  {
    id: "json",
    title: "JSON with escaped quotes",
    text: '{"msg":"user=\\"a.sharma\\" FAILED login","src":"103.21.4.77"}',
    fields: [
      ["user.name", '\\"a.sharma\\"'],
      ["src_endpoint.ip", "103.21.4.77"],
    ],
  },
  {
    id: "devanagari",
    title: "Devanagari (OT historian)",
    text: "संयंत्र=नाशिक-2 तापमान=42 status=ठीक",
    fields: [
      ["metric.value", "42"],
      ["status", "ठीक"],
    ],
  },
];

const encoder = new TextEncoder();

/** The UTF-8 byte span IF-ULPF would record for the first occurrence of `sub`. */
function byteSpanOf(text: string, sub: string): [number, number] {
  const start = encoder.encode(text.slice(0, text.indexOf(sub))).length;
  return [start, start + encoder.encode(sub).length];
}

function ThreadSample({ sample }: { sample: Sample }) {
  const raw = useRef<RawHighlighterHandle>(null);
  const [active, setActive] = useState<string | null>(null);
  const [ends, setEnds] = useState<{ from: DOMRect; to: DOMRect } | null>(null);

  const spans = sample.fields.map(([id, value]) => {
    const [startChar, endChar] = byteSpanToChar(sample.text, byteSpanOf(sample.text, value));
    return { id, startChar, endChar, tone: "muted" as const };
  });

  const hover = (id: string | null, element?: HTMLElement) => {
    setActive(id);
    const to = id ? (raw.current?.getSpanRect(id) ?? null) : null;
    setEnds(element && to ? { from: element.getBoundingClientRect(), to } : null);
  };

  return (
    <section data-sample={sample.id} className="grid grid-cols-2 gap-8 border-b border-rule py-4">
      <div>
        <h3 className="mb-2 text-meta text-ink-2">{sample.title}</h3>
        <RawHighlighter ref={raw} text={sample.text} spans={spans} activeId={active ?? undefined} />
      </div>
      <dl>
        {sample.fields.map(([id, value]) => (
          <div
            key={id}
            onMouseEnter={(event) => hover(id, event.currentTarget)}
            onMouseLeave={() => hover(null)}
            className="flex gap-4 py-1"
          >
            <dt className="font-mono text-meta text-ink-2">{id}</dt>
            <dd className="font-mono text-meta">{value}</dd>
          </div>
        ))}
      </dl>
      <ThreadOverlay from={ends?.from ?? null} to={ends?.to ?? null} />
    </section>
  );
}

const ROWS = [
  { id: "src_fw_dmz_01", eps: 6.4 },
  { id: "src_lnx_core_07", eps: 9.1 },
];

export default function ComponentsPage() {
  const push = useToast();
  const [drawer, setDrawer] = useState(false);
  const [confirm, setConfirm] = useState(false);

  return (
    <div className="flex flex-col gap-10 p-6">
      <h1 className="text-title font-semibold">Components</h1>

      <section>
        <h2 className="text-lead font-semibold">RawHighlighter and the thread</h2>
        {SAMPLES.map((sample) => (
          <ThreadSample key={sample.id} sample={sample} />
        ))}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lead font-semibold">Tiers and status</h2>
        <div className="flex gap-6">
          {TIERS.map((tier) => (
            <TierChip key={tier} tier={tier} />
          ))}
        </div>
        <MixBar counts={{ "1": 62, "2": 5, "3": 30, "4": 3 }} />
        <TierBar
          series={[
            { "1": 9, "2": 1, "3": 0, "4": 0 },
            { "1": 6, "2": 1, "3": 3, "4": 0 },
            { "1": 2, "2": 0, "3": 7, "4": 1 },
          ]}
        />
        <div className="flex gap-6">
          <StatusDot tone="good" label="Live" />
          <StatusDot tone="warn" label="Reconnecting" />
          <StatusDot tone="bad" label="Chain broken" />
          <StatusDot tone="idle" label="Not connected" />
          <Kbd>Shift+T</Kbd>
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lead font-semibold">Table, drawer, dialog, toast</h2>
        <DataTable
          columns={[
            { id: "id", header: "Source", cell: (r) => r.id, sortValue: (r) => r.id },
            { id: "eps", header: "Events/s", cell: (r) => r.eps, sortValue: (r) => r.eps, align: "right" },
          ]}
          rows={ROWS}
          rowKey={(r) => r.id}
          onActivate={() => setDrawer(true)}
        />
        <div className="flex gap-3">
          <button type="button" className="rounded-control border border-rule px-3 py-1.5" onClick={() => setConfirm(true)}>
            Open a confirm dialog
          </button>
          <button type="button" className="rounded-control border border-rule px-3 py-1.5" onClick={() => push("Contract authsrv changed")}>
            Show a toast
          </button>
        </div>
      </section>

      <Drawer open={drawer} onOpenChange={setDrawer} title="src_fw_dmz_01">
        <p>Drawer content.</p>
      </Drawer>
      <ConfirmDialog
        open={confirm}
        title="Revoke key"
        body="Revoke key k_DEMO? Anything using it is refused within 2 seconds."
        confirmLabel="Revoke"
        onConfirm={() => setConfirm(false)}
        onCancel={() => setConfirm(false)}
      />
    </div>
  );
}
