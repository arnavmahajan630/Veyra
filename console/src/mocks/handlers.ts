import { http, HttpResponse, sse } from "msw";
import type { ApiKeyRow, KeyCard, Me } from "../api/types";
import { c6Handlers, freshC6, type C6World } from "./c6";
import { DEMO_PASSWORD, KEYS, TENANTS, USERS, healthAt, overviewAt, tierHistoryAt } from "./fixtures";

const LIVE_TICK_MS = 1_000;

/** Lineage streams opened so far in this tab; only the newest one ticks. */
let lineageStreams = 0;

interface MockState {
  me: Me | null;
  tick: number;
  issued: number;
  keys: Map<string, ApiKeyRow[]>;
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
];

// MSW refuses to build SSE handlers where EventSource does not exist (jsdom). The unit
// tests never open a stream (useSSE is a no-op there), so they get the HTTP handlers only.
function streamHandlers() {
  if (typeof EventSource === "undefined") return [];
  return [
    sse<{ overview: string }>("/api/lineage/stream", ({ client, request }) => {
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
