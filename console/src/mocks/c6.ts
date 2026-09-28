// The C6 mock world: contracts, drift, drafts, replay, audit, routes and onboarding, shaped by
// control-api's response models so the pages and the Beat 2/4 flows run in `npm run dev:mock`.
import { http, HttpResponse } from "msw";
import type {
  AuditRow,
  ContractDetail,
  DiffOut,
  Draft,
  DraftEdit,
  DraftMapping,
  DriftItem,
  Me,
  ProvenanceRow,
  ReplayJob,
  Source,
  VersionDetail,
  VersionSummary,
} from "../api/types";
import {
  AUDIT,
  DRAFT_T3,
  DRIFT,
  ROUTES,
  SOURCE_AUTHSRV,
  SOURCES,
  analyzeFrames,
  contractIdFor,
  mockContracts,
  onboardDraft,
  type MockContract,
} from "./fixtures";

const WRITERS = ["admin", "pack_author"];
const APPROVERS = ["admin", "pack_approver"];
const ACTORS = ["admin", "pack_author", "pack_approver"];
const LIBRARY_PACKS = ["acme_ngfw_cef", "generic_cef", "generic_leef", "linux_sshd", "nginx_access"];
/** Events the T3 replay covers (Beat 4: "Replay 8 events"). */
const REPLAY_TOTAL = 8;
const REPLAY_STEP = 3;

export interface C6World {
  sources: Source[];
  contracts: Map<string, MockContract>;
  drift: DriftItem[];
  drafts: Map<string, Draft>;
  replays: Map<string, ReplayJob>;
  audit: AuditRow[];
  counter: number;
}

export function freshC6(): C6World {
  return {
    sources: [...SOURCES, SOURCE_AUTHSRV].map((s) => structuredClone(s)),
    contracts: new Map(mockContracts().map((c) => [c.summary.id, c])),
    drift: DRIFT.map((d) => structuredClone(d)),
    drafts: new Map([
      ["dr_t3", structuredClone(DRAFT_T3)],
      ["dr_onboard", onboardDraft("src_auth_server_01", "t_maha_power")],
    ]),
    replays: new Map(),
    audit: AUDIT.map((a) => ({ ...a })),
    counter: 0,
  };
}

export interface C6Context {
  world: () => C6World;
  me: () => Me | null;
  visible: (tenantId: string) => boolean;
}

const now = () => new Date().toISOString();
const json = (body: unknown, status = 200) => HttpResponse.json(body as never, { status });
const detail = (status: number, message: string) => HttpResponse.json({ detail: message }, { status });
const unauthorized = () => detail(401, "sign in required");
const notFound = (what: string) => detail(404, `${what} not found`);
/** control-api's `require(*roles)` refusal text (auth.py). */
const roleDenied = (allowed: string[]) =>
  detail(403, `this action needs one of the roles: ${[...allowed].sort().join(", ")}`);

function summaryOf(v: VersionDetail): VersionSummary {
  const { yaml: _yaml, compiled: _compiled, golden: _golden, lint: _lint, backtest: _backtest, ...summary } = v;
  return summary;
}

function detailOf(c: MockContract): ContractDetail {
  return { ...c.summary, versions: c.versions.map(summaryOf), history: c.history };
}

const templateIds = (yaml: string) => [...yaml.matchAll(/^\s+- id: (\S+)/gm)].map((m) => m[1] ?? "");

/** A plain line diff of two YAML texts, enough for the diff view in mock mode. */
function yamlDiff(from: string, to: string): string {
  const before = new Set(from.split("\n"));
  const after = new Set(to.split("\n"));
  const lines: string[] = [];
  for (const line of from.split("\n")) if (!after.has(line)) lines.push(`- ${line}`);
  for (const line of to.split("\n")) if (!before.has(line)) lines.push(`+ ${line}`);
  return lines.join("\n");
}

/** Re-derive provenance and type issues after an edit (the mock's stand-in for verify.py). */
function verify(draft: Draft): void {
  const rows: ProvenanceRow[] = [];
  const typeIssues: string[] = [];
  for (const template of draft.templates) {
    for (const mapping of template.response.mappings) {
      if (mapping.token === undefined) {
        rows.push({ ocsf_path: mapping.ocsf_path, ok: true, reason: "derived: const", kind: "derived" });
        continue;
      }
      const span = template.spans.find((s) => s.id === mapping.token);
      if (!span) {
        rows.push({ ocsf_path: mapping.ocsf_path, ok: false, reason: `token ${mapping.token} is not in the sample`, kind: "located" });
        continue;
      }
      rows.push({ ocsf_path: mapping.ocsf_path, ok: true, reason: "", kind: "located" });
      if (mapping.ocsf_path.endsWith(".ip") && span.kind !== "ip") {
        typeIssues.push(`${mapping.ocsf_path}: ${span.value} is not an IP address`);
      }
    }
  }
  rows.sort((a, b) => a.ocsf_path.localeCompare(b.ocsf_path));
  const ok = rows.every((r) => r.ok) && typeIssues.length === 0;
  draft.verification = { ok, compile_error: null, tiers: ok ? { "1": 8 } : { "3": 8 }, provenance: rows, type_issues: typeIssues };
}

/** One SSE frame per event, as control-api's `frame()` writes them. */
function sseBody(frames: { event: string; data: unknown }[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const frame of frames) {
        controller.enqueue(encoder.encode(`event: ${frame.event}\ndata: ${JSON.stringify(frame.data)}\n\n`));
      }
      controller.close();
    },
  });
}

export function c6Handlers({ world, me, visible }: C6Context) {
  const signedIn = () => me() !== null;
  const hasRole = (roles: string[]) => roles.includes(me()?.role ?? "");
  const record = (action: string, target: string, extra = "") => {
    const user = me();
    if (user) world().audit.unshift({ actor: user.user.email, role: user.role, action, target, detail: extra, at: now() });
  };
  const contract = (id: string) => {
    const found = world().contracts.get(id);
    return found && visible(found.summary.tenant_id) ? found : undefined;
  };
  const version = (c: MockContract, v: number) => c.versions.find((x) => x.version === v);
  const transition = (c: MockContract, v: number, action: string, from: string | null, to: string) =>
    c.history.push({ version: v, action, from_state: from, to_state: to, actor: me()?.user.email ?? "", at: now(), reason: "" });

  /** Add a canary version (a submitted draft or a cloned library pack) and return it. */
  const addCanary = (id: string, tenant: string, sources: string[], yaml: string, draftId: string | null) => {
    const w = world();
    const existing = w.contracts.get(id);
    const next = existing ? existing.summary.latest_version + 1 : 1;
    const row: VersionDetail = {
      contract_id: id, version: next, state: "canary", author: me()?.user.email ?? "",
      approved_by: null, approved_at: null, promoted_by: null, promoted_at: null, retired_reason: null,
      git_commit: `mock${next}`, created_at: now(), draft_id: draftId,
      yaml: yaml.replace(/^version: \d+$/m, `version: ${next}`), compiled: null,
      golden: { passed: true, total: 2, failed: 0, cases: [] }, lint: [], backtest: null,
    };
    const c: MockContract = existing ?? {
      summary: {
        id, tenant_id: tenant, sources, active_version: null, canary_version: null, latest_version: 0,
        latest_state: "canary", updated_at: null, updated_by: null,
      },
      versions: [],
      history: [],
    };
    c.versions.push(row);
    Object.assign(c.summary, { canary_version: next, latest_version: next, latest_state: "canary", updated_at: now(), updated_by: row.author });
    transition(c, next, "submitted", null, "canary");
    w.contracts.set(id, c);
    record("contract.submit", `${id}@${next}`);
    return row;
  };

  return [
    http.post("/api/control/sources", async ({ request }) => {
      if (!signedIn()) return unauthorized();
      if (!hasRole(WRITERS)) return roleDenied(WRITERS);
      const body = (await request.json()) as Partial<Source> & { id: string; tenant_id: string; name: string };
      if (!visible(body.tenant_id)) return notFound(`tenant ${body.tenant_id}`);
      if (world().sources.some((s) => s.id === body.id)) return detail(409, `source ${body.id} already exists`);
      const source: Source = {
        id: body.id, tenant_id: body.tenant_id, name: body.name, vendor: body.vendor ?? "custom",
        zone: body.zone ?? "core", transport: body.transport ?? "http_push", listener: body.listener ?? null,
        match_kind: body.match_kind ?? null, match_value: body.match_value ?? null, contract_id: null,
        expected_eps: body.expected_eps ?? 0, salt_buckets: 1, status: "active", created_at: now(),
      };
      world().sources.push(source);
      record("source.create", source.id);
      return json(source, 201);
    }),

    http.get("/api/control/contracts", ({ request }) => {
      if (!signedIn()) return unauthorized();
      const tenant = new URL(request.url).searchParams.get("tenant");
      const rows = [...world().contracts.values()]
        .filter((c) => visible(c.summary.tenant_id) && (!tenant || c.summary.tenant_id === tenant))
        .map((c) => c.summary)
        .sort((a, b) => a.id.localeCompare(b.id));
      return json(rows);
    }),

    http.get("/api/control/contracts/:id", ({ params }) => {
      if (!signedIn()) return unauthorized();
      const c = contract(String(params.id));
      return c ? json(detailOf(c)) : notFound(`contract ${String(params.id)}`);
    }),

    http.get("/api/control/contracts/:id/versions/:v", ({ params }) => {
      if (!signedIn()) return unauthorized();
      const c = contract(String(params.id));
      const row = c && version(c, Number(params.v));
      return row ? json(row) : notFound(`${String(params.id)}@${String(params.v)}`);
    }),

    http.get("/api/control/contracts/:id/diff", ({ params, request }) => {
      if (!signedIn()) return unauthorized();
      const c = contract(String(params.id));
      if (!c) return notFound(`contract ${String(params.id)}`);
      const url = new URL(request.url);
      const to = Number(url.searchParams.get("to") ?? c.summary.latest_version);
      const from = Number(url.searchParams.get("from") ?? to - 1);
      const a = version(c, from);
      const b = version(c, to);
      if (!a || !b) return notFound(`${c.summary.id}@${!a ? from : to}`);
      const before = templateIds(a.yaml);
      const after = templateIds(b.yaml);
      const body: DiffOut = {
        contract_id: c.summary.id,
        from_version: from,
        to_version: to,
        semantic: {
          templates_added: after.filter((t) => !before.includes(t)),
          templates_removed: before.filter((t) => !after.includes(t)),
          templates_changed: [],
          order_changed: false,
          changed_sections: ["templates"],
        },
        yaml: yamlDiff(a.yaml, b.yaml),
      };
      return json(body);
    }),

    http.post("/api/control/contracts/:id/versions/:v/approve", ({ params }) => {
      if (!signedIn()) return unauthorized();
      if (!hasRole(APPROVERS)) return roleDenied(APPROVERS);
      const c = contract(String(params.id));
      const row = c && version(c, Number(params.v));
      if (!c || !row) return notFound(`${String(params.id)}@${String(params.v)}`);
      const ref = `${c.summary.id}@${row.version}`;
      if (row.state !== "canary") return detail(409, `${ref} is ${row.state}; only a canary can be approved`);
      const email = me()?.user.email ?? "";
      if (email === row.author) {
        return detail(403, `four-eyes: ${email} submitted ${ref}, so someone else must approve it`);
      }
      if (row.approved_by === null) {
        Object.assign(row, { approved_by: email, approved_at: now() });
        transition(c, row.version, "approved", "canary", "canary");
        record("contract.approve", ref);
      }
      return json(row);
    }),

    http.post("/api/control/contracts/:id/versions/:v/promote", ({ params }) => {
      if (!signedIn()) return unauthorized();
      if (!hasRole(APPROVERS)) return roleDenied(APPROVERS);
      const c = contract(String(params.id));
      const row = c && version(c, Number(params.v));
      if (!c || !row) return notFound(`${String(params.id)}@${String(params.v)}`);
      const ref = `${c.summary.id}@${row.version}`;
      if (row.state !== "canary") return detail(409, `${ref} is ${row.state}; only a canary can be promoted`);
      if (row.approved_by === null) return detail(409, `approve ${ref} first (four-eyes)`);
      const previous = c.summary.active_version === null ? undefined : version(c, c.summary.active_version);
      if (previous) {
        Object.assign(previous, { state: "retired", retired_reason: `replaced by v${row.version}` });
        transition(c, previous.version, "retired", "active", "retired");
      }
      Object.assign(row, { state: "active", promoted_by: me()?.user.email ?? "", promoted_at: now() });
      Object.assign(c.summary, { active_version: row.version, canary_version: null, latest_state: "active", updated_at: now() });
      transition(c, row.version, "promoted", "canary", "active");
      for (const sourceId of c.summary.sources) {
        const source = world().sources.find((s) => s.id === sourceId);
        if (source) source.contract_id = c.summary.id;
      }
      // An item resolves once the active version covers it (C3): here, the version its draft became.
      for (const item of world().drift) {
        if (item.draft_id !== null && item.draft_id === row.draft_id) {
          Object.assign(item, { state: "resolved", resolved_by: ref, updated_at: now() });
        }
      }
      record("contract.promote", ref);
      return json(row);
    }),

    http.post("/api/control/contracts/:id/rollback", async ({ params, request }) => {
      if (!signedIn()) return unauthorized();
      if (!hasRole(APPROVERS)) return roleDenied(APPROVERS);
      const c = contract(String(params.id));
      if (!c) return notFound(`contract ${String(params.id)}`);
      const { to_version: to } = (await request.json()) as { to_version: number };
      const target = version(c, to);
      if (!target || target.version === c.summary.active_version) {
        return detail(409, `${c.summary.id}@${to} can't be rolled back to`);
      }
      const current = c.summary.active_version === null ? undefined : version(c, c.summary.active_version);
      if (current) {
        Object.assign(current, { state: "retired", retired_reason: `rolled back to v${to}` });
        transition(c, current.version, "rolled_back", "active", "retired");
      }
      Object.assign(target, { state: "active", retired_reason: null });
      Object.assign(c.summary, { active_version: to, latest_state: "active", updated_at: now() });
      transition(c, to, "rollback", "retired", "active");
      record("contract.rollback", `${c.summary.id}@${to}`);
      return json(target);
    }),

    http.get("/api/control/drift", ({ request }) => {
      if (!signedIn()) return unauthorized();
      const url = new URL(request.url);
      const state = url.searchParams.get("state");
      const source = url.searchParams.get("source_id");
      return json(
        world().drift.filter(
          (d) => visible(d.tenant_id) && (!state || d.state === state) && (!source || d.source_id === source),
        ),
      );
    }),

    http.get("/api/control/drift/:id", ({ params }) => {
      if (!signedIn()) return unauthorized();
      const item = world().drift.find((d) => d.drift_id === params.id && visible(d.tenant_id));
      return item ? json(item) : notFound(`drift item ${String(params.id)}`);
    }),

    http.post("/api/control/drift/:id/dismiss", ({ params }) => {
      if (!signedIn()) return unauthorized();
      if (!hasRole(ACTORS)) return roleDenied(ACTORS);
      const item = world().drift.find((d) => d.drift_id === params.id && visible(d.tenant_id));
      if (!item) return notFound(`drift item ${String(params.id)}`);
      Object.assign(item, { state: "dismissed", updated_at: now() });
      record("drift.dismiss", item.drift_id);
      return json(item);
    }),

    http.post("/api/control/drift/:id/draft", ({ params }) => {
      if (!signedIn()) return unauthorized();
      if (!hasRole(ACTORS)) return roleDenied(ACTORS);
      const w = world();
      const item = w.drift.find((d) => d.drift_id === params.id && visible(d.tenant_id));
      if (!item) return notFound(`drift item ${String(params.id)}`);
      if (!["open", "drafting", "draft_ready"].includes(item.state)) return detail(409, `drift item is ${item.state}`);
      w.counter += 1;
      const draft = structuredClone(DRAFT_T3);
      draft.draft_id = `dr_t3_${w.counter}`;
      draft.created_at = draft.updated_at = now();
      w.drafts.set(draft.draft_id, draft);
      Object.assign(item, { state: "draft_ready", draft_id: draft.draft_id, updated_at: now() });
      return json({ draft_id: draft.draft_id }, 202);
    }),

    http.get("/api/control/drafts/:id", ({ params }) => {
      if (!signedIn()) return unauthorized();
      const draft = world().drafts.get(String(params.id));
      return draft && visible(draft.tenant_id) ? json(draft) : notFound(`draft ${String(params.id)}`);
    }),

    http.patch("/api/control/drafts/:id", async ({ params, request }) => {
      if (!signedIn()) return unauthorized();
      if (!hasRole(ACTORS)) return roleDenied(ACTORS);
      const draft = world().drafts.get(String(params.id));
      if (!draft || !visible(draft.tenant_id)) return notFound(`draft ${String(params.id)}`);
      if (draft.state !== "ready") return detail(409, `draft is ${draft.state}`);
      const edit = (await request.json()) as DraftEdit;
      const template = draft.templates.find((t) => t.template_sig === edit.template_sig) ?? draft.templates[0];
      if (!template) return detail(409, "draft has no templates");
      const mappings: DraftMapping[] = edit.mappings ?? template.response.mappings;
      const unknown = mappings.find((m) => !template.request.allowed_fields.includes(m.ocsf_path));
      if (unknown) return detail(422, `${unknown.ocsf_path} is not in the field catalogue for ${template.response.class}`);
      template.response = {
        ...template.response,
        class: edit.class ?? template.response.class,
        activity: edit.activity ?? template.response.activity,
        mappings,
      };
      verify(draft);
      draft.updated_at = now();
      return json(draft);
    }),

    http.post("/api/control/drafts/:id/submit", ({ params }) => {
      if (!signedIn()) return unauthorized();
      if (!hasRole(WRITERS)) return roleDenied(WRITERS);
      const draft = world().drafts.get(String(params.id));
      if (!draft || !visible(draft.tenant_id)) return notFound(`draft ${String(params.id)}`);
      if (draft.state !== "ready") return detail(409, `draft is ${draft.state}`);
      if (!draft.verification?.ok) return detail(422, "the draft fails its provenance check");
      const id = draft.contract_id ?? contractIdFor(draft.source_id ?? "src_unknown_01");
      const row = addCanary(id, draft.tenant_id, draft.source_id ? [draft.source_id] : [], draft.yaml, draft.draft_id);
      Object.assign(draft, { state: "submitted", updated_at: now() });
      return json(row, 201);
    }),

    http.post("/api/control/replay", async ({ request }) => {
      if (!signedIn()) return unauthorized();
      if (!hasRole(ACTORS)) return roleDenied(ACTORS);
      const body = (await request.json()) as { contract_id: string; template_sigs?: string[] };
      const c = contract(body.contract_id);
      if (!c) return notFound(`contract ${body.contract_id}`);
      if (c.summary.active_version === null) return detail(409, `${c.summary.id} has no active version yet`);
      const w = world();
      w.counter += 1;
      const job: ReplayJob = {
        job_id: `rp_${w.counter}`, contract_id: c.summary.id, tenant_id: c.summary.tenant_id, state: "publishing",
        total: REPLAY_TOTAL, published: REPLAY_TOTAL, normalized: 0,
        params: { template_sigs: body.template_sigs ?? [], source_id: null },
        created_by: me()?.user.email ?? null, created_at: now(), finished_at: null, detail: "",
      };
      w.replays.set(job.job_id, job);
      record("replay.start", c.summary.id, job.job_id);
      return json(job, 202);
    }),

    http.get("/api/control/replay", ({ request }) => {
      if (!signedIn()) return unauthorized();
      const contractId = new URL(request.url).searchParams.get("contract_id");
      return json([...world().replays.values()].filter((j) => visible(j.tenant_id) && (!contractId || j.contract_id === contractId)));
    }),

    // Each read advances the job, so polling alone finishes it (Review Focus 3).
    http.get("/api/control/replay/:id", ({ params }) => {
      if (!signedIn()) return unauthorized();
      const job = world().replays.get(String(params.id));
      if (!job || !visible(job.tenant_id)) return notFound(`replay ${String(params.id)}`);
      if (job.state !== "done") {
        job.normalized = Math.min(job.total, job.normalized + REPLAY_STEP);
        job.state = job.normalized >= job.total ? "done" : "normalizing";
        if (job.state === "done") job.finished_at = now();
      }
      return json(job);
    }),

    http.get("/api/control/audit", () => (signedIn() ? json(world().audit) : unauthorized())),

    http.get("/api/control/routes", () => (signedIn() ? json({ routes: ROUTES }) : unauthorized())),

    http.post("/api/control/onboarding/use-library", async ({ request }) => {
      if (!signedIn()) return unauthorized();
      if (!hasRole(WRITERS)) return roleDenied(WRITERS);
      const body = (await request.json()) as { source_id: string; pack: string };
      const source = world().sources.find((s) => s.id === body.source_id && visible(s.tenant_id));
      if (!source) return notFound(`source ${body.source_id}`);
      if (!LIBRARY_PACKS.includes(body.pack)) return notFound(`library pack ${body.pack}`);
      const id = contractIdFor(source.id);
      const yaml = `contract: ${id}\nversion: 1\ntenant: ${source.tenant_id}\nsources: [${source.id}]\n# cloned from library/${body.pack}.yaml\nprovenance:\n  drafted_by: library\n`;
      return json(addCanary(id, source.tenant_id, [source.id], yaml, null), 201);
    }),

    http.post("/api/control/onboarding/analyze", async ({ request }) => {
      if (!signedIn()) return unauthorized();
      if (!hasRole(WRITERS)) return roleDenied(WRITERS);
      const body = (await request.json()) as { source_id: string; samples: string[] };
      const source = world().sources.find((s) => s.id === body.source_id && visible(s.tenant_id));
      if (!source) return notFound(`source ${body.source_id}`);
      world().drafts.set("dr_onboard", onboardDraft(source.id, source.tenant_id));
      return new HttpResponse(sseBody(analyzeFrames(source.id)), {
        headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
      });
    }),
  ];
}
