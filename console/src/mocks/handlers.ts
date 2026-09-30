import { http, HttpResponse, sse } from "msw";
import type { ApiKeyRow, DemoStageStatus, KeyCard, LedgerRoot, Me, ResetStatus } from "../api/types";
import { c6Handlers, freshC6, type C6World } from "./c6";
import { DEMO_PASSWORD, KEYS, TENANTS, USERS, healthAt, overviewAt, tierHistoryAt } from "./fixtures";
import {
  AUTH_UID,
  EVENTS,
  OT_UID,
  ROOTS,
  rootFixture,
  searchFixture,
  verifyOk,
  verifyPending,
  verifyTampered,
} from "./lineageFixtures";

const LIVE_TICK_MS = 1_000;
/** How many overview ticks pass between two simulated window seals. */
const ROOT_EVERY_TICKS = 5;

/** The reset's steps, revealed one per poll so the panel's progress list is exercised. */
const RESET_STEPS: { name: string; ok: boolean; ms: number; detail: string }[] = [
  { name: "Pause baseline traffic", ok: true, ms: 50, detail: "baseline paused" },
  { name: "Reseed control plane, contracts and inventory", ok: true, ms: 420, detail: "2 tenants, 3 users, 2 sources" },
  { name: "Recreate Kafka topics", ok: true, ms: 1250, detail: "deleted 14, 14 created" },
  { name: "Truncate the lineage index", ok: true, ms: 380, detail: "9 table(s) truncated in veyra" },
  { name: "Wipe vault segments, ledger and tamper backups", ok: true, ms: 110, detail: "12 segment(s) removed; keys kept" },
  { name: "Re-create the immudb database", ok: true, ms: 600, detail: "veyra_1790000000: created" },
  { name: "Clear Wazuh indices, sinks and saved objects", ok: true, ms: 490, detail: "indices dropped; objects imported" },
  { name: "Restart the stateful consumers", ok: true, ms: 14200, detail: "restarted 6" },
  { name: "Resume baseline and pre-warm", ok: true, ms: 890, detail: "baseline resumed; ollama warmed" },
];

/** Lineage streams opened so far in this tab; only the newest one ticks. */
let lineageStreams = 0;

interface MockState {
  me: Me | null;
  tick: number;
  issued: number;
  keys: Map<string, ApiKeyRow[]>;
  /** The event the demo has tampered, if any (B5/B6 AC3). */
  tampered: string | null;
  /** The signed-root ledger; the lineage stream appends to it (B6 AC4). */
  roots: LedgerRoot[];
  /** The last stage triggered, with its `expect` results (B7). */
  stage: DemoStageStatus | null;
  /** The reset in progress, revealed a step at a time (B7). */
  reset: ResetStatus | null;
  /** Sources, contracts, drift, drafts, replay jobs and audit (C6). */
  c6: C6World;
}

// A real session is a cookie and survives a reload; the mock keeps the signed-in email in
// sessionStorage for the same effect (mock mode only; the tests clear it).
const SESSION_KEY = "veyra.mock.session";

function rememberedUser(): Me | null {
  try {
    const email = typeof sessionStorage === "undefined" ? null : sessionStorage.getItem(SESSION_KEY);
    return email ? (USERS[email] ?? null) : null;
  } catch {
    return null;
  }
}

function setMe(me: Me | null): void {
  state.me = me;
  try {
    if (typeof sessionStorage === "undefined") return;
    if (me) sessionStorage.setItem(SESSION_KEY, me.user.email);
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // Storage unavailable: the session lasts until the page reloads.
  }
}

function freshState(): MockState {
  return {
    me: rememberedUser(),
    tick: 0,
    issued: 0,
    // Which event the demo has tampered, so verify answers red for it and green again
    // after untamper — the AC3 loop, reproduced without a vault.
    tampered: null,
    roots: [...ROOTS],
    stage: null,
    reset: null,
    keys: new Map(Object.entries(KEYS).map(([source, rows]) => [source, [...rows]])),
    c6: freshC6(),
  };
}

const state = freshState();

export function resetMockState(): void {
  setMe(null);
  Object.assign(state, freshState());
}

export function signInAs(email: string): Me {
  const me = USERS[email];
  if (!me) throw new Error(`no mock user ${email}`);
  setMe(me);
  return me;
}

function visible(tenantId: string): boolean {
  return state.me !== null && (state.me.tenant === "*" || state.me.tenant === tenantId);
}

/** The tenant a lineage request sees: platform users choose with ?tenant=, everyone else is pinned. */
function scopeOf(request: Request): string | null {
  const asked = new URL(request.url).searchParams.get("tenant");
  return state.me?.tenant === "*" ? asked : (state.me?.tenant ?? null);
}

/** Key issue and revoke land in the C6 audit log, as control-api records them. */
function audit(action: string, target: string, detail: string): void {
  if (!state.me) return;
  state.c6.audit.unshift({ actor: state.me.user.email, role: state.me.role, action, target, detail, at: new Date().toISOString() });
}

const unauthorized = () => HttpResponse.json({ detail: "sign in required" }, { status: 401 });
const notFound = (what: string) => HttpResponse.json({ detail: `${what} not found` }, { status: 404 });

const httpHandlers = [
  http.post("/api/control/auth/login", async ({ request }) => {
    const body = (await request.json()) as { email?: string; password?: string };
    const me = body.email ? USERS[body.email] : undefined;
    if (!me || body.password !== DEMO_PASSWORD) {
      return HttpResponse.json({ detail: "wrong email or password" }, { status: 401 });
    }
    setMe(me);
    return HttpResponse.json(me);
  }),

  http.post("/api/control/auth/logout", () => {
    setMe(null);
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("/api/control/auth/me", () => (state.me ? HttpResponse.json(state.me) : unauthorized())),

  http.post("/api/control/auth/demo-switch", async ({ request }) => {
    if (!state.me?.demo_mode) return notFound("demo mode");
    const { email } = (await request.json()) as { email: string };
    const me = USERS[email];
    if (!me) return notFound(`demo user ${email}`);
    setMe(me);
    return HttpResponse.json(me);
  }),

  http.get("/api/control/tenants", () =>
    state.me ? HttpResponse.json(TENANTS.filter((t) => visible(t.id))) : unauthorized(),
  ),

  http.get("/api/control/sources", ({ request }) => {
    if (!state.me) return unauthorized();
    const tenant = new URL(request.url).searchParams.get("tenant");
    return HttpResponse.json(
      state.c6.sources.filter((s) => visible(s.tenant_id) && (!tenant || s.tenant_id === tenant)),
    );
  }),

  http.get("/api/control/sources/:sourceId/keys", ({ params }) => {
    if (!state.me) return unauthorized();
    const source = state.c6.sources.find((s) => s.id === params.sourceId);
    if (!source || !visible(source.tenant_id)) return notFound("source");
    return HttpResponse.json(state.keys.get(source.id) ?? []);
  }),

  http.post("/api/control/sources/:sourceId/keys", ({ params }) => {
    if (!state.me) return unauthorized();
    const source = state.c6.sources.find((s) => s.id === params.sourceId);
    if (!source || !visible(source.tenant_id)) return notFound("source");
    state.issued += 1;
    const keyId = `k_MOCK${String(state.issued).padStart(4, "0")}`;
    const secret = `veyra_mock${String(state.issued).padStart(28, "0")}`;
    const row: ApiKeyRow = {
      key_id: keyId,
      source_id: source.id,
      status: "active",
      quota_eps: 500,
      created_by: state.me.user.email,
      created_at: new Date().toISOString(),
      revoked_at: null,
    };
    state.keys.set(source.id, [...(state.keys.get(source.id) ?? []), row]);
    audit("key.create", keyId, `source=${source.id}`);
    const hec = "http://localhost:8088/services/collector/event";
    const card: KeyCard = {
      key_id: keyId,
      secret,
      endpoints: { hec_url: hec, batch_url: "http://localhost:8088/v1/batch", syslog: null },
      curl_example: `curl -s ${hec} -H 'Authorization: Splunk ${secret}' -d '{"event":"<your log line>"}'`,
    };
    return HttpResponse.json(card, { status: 201 });
  }),

  http.post("/api/control/keys/:keyId/revoke", ({ params }) => {
    if (!state.me) return unauthorized();
    for (const rows of state.keys.values()) {
      const row = rows.find((r) => r.key_id === params.keyId);
      if (row) {
        row.status = "revoked";
        row.revoked_at = new Date().toISOString();
        audit("key.revoke", row.key_id, "");
        return HttpResponse.json({ key_id: row.key_id, status: "revoked" });
      }
    }
    return notFound("key");
  }),

  http.get("/api/lineage/overview", ({ request }) => {
    if (!state.me) return unauthorized();
    const scope = scopeOf(request);
    return HttpResponse.json({ ...overviewAt(state.tick, Date.now(), scope), tier_history: tierHistoryAt(scope) });
  }),

  http.get("/api/lineage/sources", ({ request }) =>
    state.me ? HttpResponse.json(healthAt(Date.now(), scopeOf(request))) : unauthorized(),
  ),

  // B6 Lineage Search
  http.get("/api/lineage/search", ({ request }) => {
    if (!state.me) return unauthorized();
    const q = new URL(request.url).searchParams.get("q") || "";
    return HttpResponse.json(searchFixture(q));
  }),

  // B6 Event Detail
  http.get("/api/lineage/events/:uid", ({ params }) => {
    if (!state.me) return unauthorized();
    const event = EVENTS[String(params.uid)];
    return event ? HttpResponse.json(event) : notFound("event");
  }),

  // B6 Evidence Verification. The report reflects the tamper state, so Shift+T really
  // does turn the chain red and Untamper really does turn it green again.
  http.get("/api/evidence/:uid/verify", ({ params }) => {
    if (!state.me) return unauthorized();
    const uid = String(params.uid);
    if (uid === OT_UID) return HttpResponse.json(verifyPending(uid));
    return HttpResponse.json(state.tampered === uid ? verifyTampered(uid) : verifyOk(uid));
  }),

  // B6 Evidence Roots
  http.get("/api/evidence/roots", ({ request }) => {
    if (!state.me) return unauthorized();
    const limit = Number(new URL(request.url).searchParams.get("limit") ?? 50);
    const roots = state.roots.slice(0, Math.max(1, limit));
    return HttpResponse.json({
      count: state.roots.length,
      ledger: "data/vault/roots/ledger.ndjson",
      audit_status: state.roots.every((r) => r.chain_ok !== false) ? "PASS" : "FAIL",
      roots,
    });
  }),

  // B6 Public Key
  http.get("/api/evidence/pubkey", () => {
    return new HttpResponse(
      "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAf8Kq+P7m2N3h5lG+9aQ3eK9sJ2Y1u8v7w0x9z8A1b2c=\n-----END PUBLIC KEY-----\n",
      { headers: { "content-type": "text/plain" } }
    );
  }),

  // B6 Export
  http.post("/api/evidence/export/:uid", () => {
    const dummyZip = new Blob([new Uint8Array([80, 75, 5, 6, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])], {
      type: "application/zip",
    });
    return new HttpResponse(dummyZip, {
      headers: {
        "content-disposition": 'attachment; filename="veyra-evidence.zip"',
        "x-veyra-verified": "true",
      },
    });
  }),

  // B7 Demo Endpoints
  http.get("/api/demo/scenario", () => {
    if (!state.me?.demo_mode) return notFound("demo mode");
    return HttpResponse.json({
      scenario: "sih_main",
      stages: {
        "1": { title: "Hook", expects: [] },
        "2": { title: "Onboard Auth Server", expects: [] },
        "3": {
          title: "Log storm",
          expects: ["tier=3 AND source_id='src_authsrv_01' >= 8", "wazuh rule 100111 for 45.12.3.9"],
        },
        "4": { title: "Drift loop", expects: ["drift open for src_authsrv_01"] },
        "5": { title: "Traceability", expects: [] },
        "6": { title: "Close", expects: [] },
      },
    });
  }),

  // A stage is idempotent: re-triggering a running stage is a no-op, so the mock
  // answers ok:false rather than starting it twice.
  http.post("/api/demo/stage/:id", ({ params }) => {
    if (!state.me?.demo_mode) return notFound("demo mode");
    const stage = Number(params.id);
    if (stage < 1 || stage > 6) return HttpResponse.json({ detail: "stage must be 1..6" }, { status: 400 });
    if (state.stage?.state === "running") return HttpResponse.json({ ok: false, stage });
    state.stage = {
      stage,
      state: "done",
      results: stage === 3
        ? { "tier=3 AND source_id='src_authsrv_01' >= 8": true, "wazuh rule 100111 for 45.12.3.9": true }
        : stage === 4
          ? { "drift open for src_authsrv_01": true }
          : {},
    };
    return HttpResponse.json({ ok: true, stage });
  }),

  http.get("/api/demo/stage/status", () => {
    if (!state.me?.demo_mode) return notFound("demo mode");
    return HttpResponse.json(state.stage ?? { stage: 0, state: "idle", results: {} });
  }),

  http.get("/api/demo/preflight", () => {
    return HttpResponse.json([
      { check: "Containers Healthy", status: "PASS", detail: "All core containers healthy" },
      { check: "Host Memory", status: "PASS", detail: "4.8 GB free" },
      { check: "Ollama Model", status: "PASS", detail: "Qwen 2.5 Coder resident in cache" },
      { check: "Wazuh Rules", status: "PASS", detail: "Rules 100100-100130 verified via wazuh-logtest" },
      { check: "Clock Offset", status: "PASS", detail: "< 200 ms between containers" },
      { check: "Disk Storage", status: "PASS", detail: "18.2 GB available" },
      { check: "Ollama GPU", status: "WARN", detail: "model resident on CPU; drafts fall back to the cache" },
      { check: "Baseline EPS", status: "PASS", detail: "within 12% of the target" },
    ]);
  }),

  // The reset runs in the background and is polled, so the mock does the same.
  http.post("/api/demo/reset", () => {
    if (!state.me?.demo_mode) return notFound("demo mode");
    state.reset = { running: true, ready: false, ok: null, seconds: 0, over_budget: false, steps: [] };
    return HttpResponse.json({ started: true, budget_s: 90 });
  }),

  http.get("/api/demo/reset/status", () => {
    if (!state.me?.demo_mode) return notFound("demo mode");
    if (!state.reset) {
      return HttpResponse.json({
        running: false, ready: true, ok: null, seconds: 0, over_budget: false, steps: [],
      });
    }
    // Reveal a few more steps per poll, then settle — the console must cope with a
    // part-finished list and a finished one.
    const next = RESET_STEPS.slice(0, state.reset.steps.length + 4);
    const done = next.length === RESET_STEPS.length;
    state.reset = {
      running: !done,
      ready: done,
      ok: done ? true : null,
      seconds: next.reduce((total, step) => total + step.ms, 0) / 1000,
      over_budget: false,
      steps: next,
    };
    return HttpResponse.json(state.reset);
  }),

  http.get("/api/demo/tamper/active", () => {
    if (!state.me?.demo_mode) return notFound("demo mode");
    return HttpResponse.json({
      active: state.tampered
        ? [{ mode: "insider_rewrite", event_uid: state.tampered, at: new Date().toISOString() }]
        : [],
      modes: ["naive_flip", "insider_rewrite", "segment_delete", "root_rewrite"],
    });
  }),

  http.post("/api/demo/hotkeys", async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as { combos?: string[] };
    return HttpResponse.json({ ok: true, combos: body.combos ?? [] });
  }),

  http.get("/api/demo/baseline", () =>
    HttpResponse.json({
      paused: false,
      target_eps: 15,
      actual_eps: 14.2,
      streams: {
        ntro_fw: { sent: 120, errors: 0, eps: 7.1 },
        ntro_lnx: { sent: 118, errors: 0, eps: 7.1 },
      },
    }),
  ),

  http.post("/api/demo/baseline/pause", () => HttpResponse.json({ paused: true, target_eps: 15, actual_eps: 0, streams: {} })),
  http.post("/api/demo/baseline/resume", () => HttpResponse.json({ paused: false, target_eps: 15, actual_eps: 15, streams: {} })),

  http.post("/api/demo/tamper", async ({ request }) => {
    if (!state.me?.demo_mode) return notFound("demo mode");
    const body = (await request.json().catch(() => ({}))) as { mode?: string; event_uid?: string };
    state.tampered = body.event_uid ?? AUTH_UID;
    return HttpResponse.json({
      target: `seg_raw.custom_1_…14 record 14 (${state.tampered})`,
      mode: body.mode ?? "insider_rewrite",
      detail: "the stored raw bytes were rewritten and the segment re-encrypted",
    });
  }),

  http.post("/api/demo/untamper", () => {
    if (!state.me?.demo_mode) return notFound("demo mode");
    const restored = state.tampered;
    state.tampered = null;
    return HttpResponse.json({ ok: true, restored: restored ? [restored] : [], operations_undone: restored ? 1 : 0 });
  }),
];

// MSW refuses to build SSE handlers where EventSource does not exist (jsdom). The unit
// tests never open a stream (useSSE is a no-op there), so they get the HTTP handlers only.
function streamHandlers() {
  if (typeof EventSource === "undefined") return [];
  return [
    sse<{ overview: string; root: string }>("/api/lineage/stream", ({ client, request }) => {
      const scope = scopeOf(request);
      // MSW tells a handler neither that the page closed its stream (the abort signal stays
      // quiet) nor that a write failed (it only logs). A tab holds one lineage stream at a
      // time, so a newer stream, or signing out, retires this one's timer.
      const generation = ++lineageStreams;
      const timer = setInterval(() => {
        if (generation !== lineageStreams || !state.me) {
          clearInterval(timer);
          return;
        }
        state.tick += 1;
        client.send({ event: "overview", data: JSON.stringify(overviewAt(state.tick, Date.now(), scope)) });
        // A window seals every ROOT_EVERY_TICKS ticks: the Evidence page must show new
        // roots arriving live, not on a refresh (AC4).
        if (state.tick % ROOT_EVERY_TICKS === 0) {
          const next = rootFixture(
            `w_${1790000060 + state.roots.length * 60}`,
            1790000060 + state.roots.length * 60,
            4 + (state.tick % 5),
            `${(state.tick % 10)}${"c4d81a29384758d6e9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1".slice(0, 63)}`,
          );
          state.roots = [next, ...state.roots];
          client.send({ event: "root", data: JSON.stringify(next) });
        }
      }, LIVE_TICK_MS);
      request.signal.addEventListener("abort", () => clearInterval(timer));
    }),
    sse("/api/control/stream", () => {
      // Control-side events arrive only on mutations; the mock stream stays open and quiet.
    }),
  ];
}

const c6 = c6Handlers({ world: () => state.c6, me: () => state.me, visible });

export const handlers = [...httpHandlers, ...c6, ...streamHandlers()];
