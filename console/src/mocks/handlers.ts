import { http, HttpResponse, sse } from "msw";
import type { ApiKeyRow, KeyCard, Me } from "../api/types";
import { DEMO_PASSWORD, KEYS, SOURCES, TENANTS, USERS, healthAt, overviewAt } from "./fixtures";

const LIVE_TICK_MS = 1_000;

interface MockState {
  me: Me | null;
  tick: number;
  issued: number;
  keys: Map<string, ApiKeyRow[]>;
}

function freshState(): MockState {
  return {
    me: null,
    tick: 0,
    issued: 0,
    keys: new Map(Object.entries(KEYS).map(([source, rows]) => [source, [...rows]])),
  };
}

const state = freshState();

export function resetMockState(): void {
  Object.assign(state, freshState());
}

export function signInAs(email: string): Me {
  const me = USERS[email];
  if (!me) throw new Error(`no mock user ${email}`);
  state.me = me;
  return me;
}

function visible(tenantId: string): boolean {
  return state.me !== null && (state.me.tenant === "*" || state.me.tenant === tenantId);
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
    state.me = me;
    return HttpResponse.json(me);
  }),

  http.post("/api/control/auth/logout", () => {
    state.me = null;
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("/api/control/auth/me", () => (state.me ? HttpResponse.json(state.me) : unauthorized())),

  http.post("/api/control/auth/demo-switch", async ({ request }) => {
    if (!state.me?.demo_mode) return notFound("demo mode");
    const { email } = (await request.json()) as { email: string };
    const me = USERS[email];
    if (!me) return notFound(`demo user ${email}`);
    state.me = me;
    return HttpResponse.json(me);
  }),

  http.get("/api/control/tenants", () =>
    state.me ? HttpResponse.json(TENANTS.filter((t) => visible(t.id))) : unauthorized(),
  ),

  http.get("/api/control/sources", ({ request }) => {
    if (!state.me) return unauthorized();
    const tenant = new URL(request.url).searchParams.get("tenant");
    return HttpResponse.json(
      SOURCES.filter((s) => visible(s.tenant_id) && (!tenant || s.tenant_id === tenant)),
    );
  }),

  http.get("/api/control/sources/:sourceId/keys", ({ params }) => {
    if (!state.me) return unauthorized();
    const source = SOURCES.find((s) => s.id === params.sourceId);
    if (!source || !visible(source.tenant_id)) return notFound("source");
    return HttpResponse.json(state.keys.get(source.id) ?? []);
  }),

  http.post("/api/control/sources/:sourceId/keys", ({ params }) => {
    if (!state.me) return unauthorized();
    const source = SOURCES.find((s) => s.id === params.sourceId);
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
        return HttpResponse.json({ key_id: row.key_id, status: "revoked" });
      }
    }
    return notFound("key");
  }),

  http.get("/api/lineage/overview", () =>
    state.me ? HttpResponse.json(overviewAt(state.tick)) : unauthorized(),
  ),

  http.get("/api/lineage/sources", () => (state.me ? HttpResponse.json(healthAt()) : unauthorized())),
];

// MSW refuses to build SSE handlers where EventSource does not exist (jsdom). The unit
// tests never open a stream (useSSE is a no-op there), so they get the HTTP handlers only.
function streamHandlers() {
  if (typeof EventSource === "undefined") return [];
  return [
    sse<{ overview: string }>("/api/lineage/stream", ({ client, request }) => {
      const timer = setInterval(() => {
        state.tick += 1;
        client.send({ event: "overview", data: JSON.stringify(overviewAt(state.tick)) });
      }, LIVE_TICK_MS);
      request.signal.addEventListener("abort", () => clearInterval(timer));
    }),
    sse("/api/control/stream", () => {
      // Control-side events arrive only on mutations; the mock stream stays open and quiet.
    }),
  ];
}

export const handlers = [...httpHandlers, ...streamHandlers()];
