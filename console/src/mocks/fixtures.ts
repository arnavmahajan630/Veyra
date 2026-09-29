// The pre-demo world of 04_DEMO_SCRIPT §2, as the APIs would return it.
import type {
  ApiKeyRow,
  AuditRow,
  ContractSummary,
  Draft,
  DriftItem,
  Me,
  Overview,
  OverviewSource,
  RouteSpec,
  Source,
  SourceHealth,
  Tenant,
  TierCounts,
  Transition,
  VersionDetail,
} from "../api/types";

export const DEMO_PASSWORD = "veyra-demo";
const CREATED = "2026-09-27T09:00:00.000000000Z";

export const USERS: Record<string, Me> = {
  "admin@veyra": {
    user: { email: "admin@veyra", name: "Admin" },
    role: "admin",
    tenant: "*",
    demo_mode: true,
  },
  "author@maha": {
    user: { email: "author@maha", name: "Maha author" },
    role: "pack_author",
    tenant: "t_maha_power",
    demo_mode: true,
  },
  "approver@veyra": {
    user: { email: "approver@veyra", name: "Approver" },
    role: "pack_approver",
    tenant: "*",
    demo_mode: true,
  },
};

export const TENANTS: Tenant[] = [
  { id: "t_maha_power", name: "Maha Power Corp", created_at: CREATED },
  { id: "t_ntro_core", name: "NTRO Core Ops", created_at: CREATED },
];

export const SOURCES: Source[] = [
  {
    id: "src_fw_dmz_01",
    tenant_id: "t_ntro_core",
    name: "Acme NGFW (DMZ)",
    vendor: "acme_ngfw",
    zone: "dmz",
    transport: "syslog_tcp",
    listener: "dmz-tcp",
    match_kind: "syslog_host",
    match_value: "fw-dmz-01",
    contract_id: "acme_ngfw_cef",
    expected_eps: 6,
    salt_buckets: 1,
    status: "active",
    created_at: CREATED,
  },
  {
    id: "src_lnx_core_07",
    tenant_id: "t_ntro_core",
    name: "Linux sshd (core)",
    vendor: "linux",
    zone: "core",
    transport: "syslog_udp",
    listener: "core-udp",
    match_kind: "syslog_host",
    match_value: "core-lnx-07",
    contract_id: "linux_sshd",
    expected_eps: 9,
    salt_buckets: 1,
    status: "active",
    created_at: CREATED,
  },
];

export const KEYS: Record<string, ApiKeyRow[]> = { src_fw_dmz_01: [], src_lnx_core_07: [], src_authsrv_01: [] };

export function tiers(t1: number, t2: number, t3: number, t4: number): TierCounts {
  return { "1": t1, "2": t2, "3": t3, "4": t4 };
}

const tenantOf = (sourceId: string) => SOURCES.find((s) => s.id === sourceId)?.tenant_id;

/** Unregistered traffic lands as tier 3 but belongs to no source, so only the all-tenants view counts it. */
const UNREGISTERED_TIER3 = 120;

/**
 * A deterministic overview for SSE tick `tick`, seen from `tenant` (null = every tenant).
 * Numbers move a little every tick; totals are the visible sources' counts.
 */
export function overviewAt(tick: number, now: number = Date.now(), tenant: string | null = null): Overview {
  const iso = new Date(now).toISOString();
  const all: OverviewSource[] = [
    { source_id: "src_fw_dmz_01", zone: "dmz", eps: 6 + (tick % 2), tiers: tiers(3600 + tick * 6, 20, 0, 2), last_seen: iso },
    { source_id: "src_lnx_core_07", zone: "core", eps: 9 + (tick % 3), tiers: tiers(5400 + tick * 7, 20 + tick, 0, 4), last_seen: iso },
  ];
  const sources = tenant ? all.filter((s) => tenantOf(s.source_id) === tenant) : all;
  const sum = (tier: keyof TierCounts) => sources.reduce((total, s) => total + s.tiers[tier], 0);
  const busy = sources.length > 0;
  return {
    eps_1m: tenant ? sources.reduce((total, s) => total + s.eps, 0) : 15 + (tick % 4),
    totals_by_tier: tiers(sum("1"), sum("2"), sum("3") + (tenant ? 0 : UNREGISTERED_TIER3 + (tick % 3)), sum("4")),
    sources,
    routes: [
      { route_id: "wazuh_main", delivered_per_min: busy ? 900 + tick : 0, failed_per_min: 0, lag_s: busy ? 0.4 : null, breaker: "closed" },
      { route_id: "partner_masked", delivered_per_min: 0, failed_per_min: 0, lag_s: null, breaker: "closed" },
    ],
    vault: {
      segments: 40 + Math.floor(tick / 20),
      last_sealed_at: new Date(now - 7_000).toISOString(),
      last_root: { window_id: "w_1790496000", window_end: new Date(now - 30_000).toISOString(), immudb_verified: true },
      chain_ok: true,
    },
    as_of: iso,
  };
}

/**
 * 15 minutes of per-second tier counts for the HTTP overview, so the tier bar opens full.
 * Around minute 9 an unknown message shape bursts in as tier 3, as in the drift beat.
 */
export function tierHistoryAt(tenant: string | null = null): TierCounts[] {
  const visible = tenant ? SOURCES.filter((s) => s.tenant_id === tenant).length : SOURCES.length;
  if (visible === 0) return Array.from({ length: 900 }, () => tiers(0, 0, 0, 0));
  return Array.from({ length: 900 }, (_, i) => {
    const burst = i >= 540 && i < 600 ? 4 + (i % 3) : 0;
    return tiers(12 + (i % 4), i % 6 === 0 ? 1 : 0, burst + (!tenant && i % 9 === 0 ? 1 : 0), i % 120 === 0 ? 1 : 0);
  });
}

export function healthAt(now: number = Date.now(), tenant: string | null = null): SourceHealth[] {
  const seen = new Date(now - 2_000).toISOString();
  const rows: SourceHealth[] = [
    {
      source_id: "src_fw_dmz_01",
      tenant_id: "t_ntro_core",
      zone: "dmz",
      transport: "syslog_tcp",
      contract_ref: "acme_ngfw_cef@1",
      expected_eps: 6,
      actual_eps: 6.4,
      last_seen: seen,
      tiers: tiers(3600, 20, 0, 2),
      clock_skew_p50_ms: -812,
    },
    {
      source_id: "src_lnx_core_07",
      tenant_id: "t_ntro_core",
      zone: "core",
      transport: "syslog_udp",
      contract_ref: "linux_sshd@1",
      expected_eps: 9,
      actual_eps: 9.1,
      last_seen: seen,
      tiers: tiers(5400, 20, 0, 4),
      clock_skew_p50_ms: 140,
    },
  ];
  return tenant ? rows.filter((r) => r.tenant_id === tenant) : rows;
}

// ---- C6: contracts, drift, drafts, audit, routes (the demo's authsrv story; Beat 2 is done)

export const SOURCE_AUTHSRV: Source = {
  id: "src_authsrv_01",
  tenant_id: "t_maha_power",
  name: "Maha Power auth server",
  vendor: "custom",
  zone: "core",
  transport: "http_push",
  listener: null,
  match_kind: null,
  match_value: null,
  contract_id: "authsrv",
  expected_eps: 0,
  salt_buckets: 1,
  status: "active",
  created_at: CREATED,
};

/** control-api's `contract_id_for`: drop `src_` and a trailing `_NN` (C4, TC32). */
export function contractIdFor(sourceId: string): string {
  return sourceId.replace(/^src_/, "").replace(/_\d+$/, "");
}

const AUTHSRV_HEAD = `contract: authsrv
version: 1
tenant: t_maha_power
sources: [src_authsrv_01]
description: Maha Power auth server (custom app, syslog-wrapped JSON body)
envelope:
  - syslog: {variant: auto}
  - json: {text_field: msg}
time:
  field: syslog.timestamp
  formats: ["%b %d %H:%M:%S"]
  timezone: Asia/Kolkata
  year: infer_from_received
templates:
  - id: auth_ok
    pattern: 'user=<user> OK login from <src_ip:ip> via <dst_ip:ip>'
    class: authentication
    activity: logon
    map:
      user.name: $user
      src_endpoint.ip: $src_ip
      dst_endpoint.ip: $dst_ip
      status_id: {const: 1}
  - id: session_closed
    pattern: 'session <session_id:int> closed for <user> after <seconds:int>s'
    class: authentication
    activity: logoff
    map:
      user.name: $user
      status_id: {const: 1}
    unmapped: [session_id, seconds]
`;

const AUTHSRV_TAIL = `required: [time, user.name]
pii: [user.name, src_endpoint.ip]
provenance:
  drafted_by: llm
`;

const AUTH_FAILED = `  - id: logon_failed
    pattern: 'user=<user> FAILED login from <src_ip:ip> via <dst_ip:ip> attempts:<attempts:int>'
    class: authentication
    activity: logon
    map:
      user.name: $user
      src_endpoint.ip: $src_ip
      dst_endpoint.ip: $dst_ip
      status_id: {const: 2}
    unmapped: [attempts]
`;

export const AUTHSRV_V1_YAML = AUTHSRV_HEAD + AUTHSRV_TAIL;
export const AUTHSRV_V2_YAML = AUTHSRV_HEAD.replace("version: 1", "version: 2") + AUTH_FAILED + AUTHSRV_TAIL;

function libraryYaml(id: string, source: string, pattern: string): string {
  return `contract: ${id}\nversion: 1\ntenant: t_ntro_core\nsources: [${source}]\ntemplates:\n  - id: main\n    pattern: '${pattern}'\n`;
}

export interface MockContract {
  summary: ContractSummary;
  versions: VersionDetail[];
  history: Transition[];
}

function activeVersion(id: string, yaml: string, author: string, approver: string, at: string): VersionDetail {
  return {
    contract_id: id,
    version: 1,
    state: "active",
    author,
    approved_by: approver,
    approved_at: at,
    promoted_by: approver,
    promoted_at: at,
    retired_reason: null,
    git_commit: `c0ffee${id.length}`,
    created_at: at,
    draft_id: null,
    yaml,
    compiled: null,
    golden: { passed: true, total: 3, failed: 0, cases: [] },
    lint: [],
    backtest: null,
  };
}

function activeContract(id: string, tenant: string, source: string, yaml: string, author: string, at: string): MockContract {
  return {
    summary: {
      id,
      tenant_id: tenant,
      sources: [source],
      active_version: 1,
      canary_version: null,
      latest_version: 1,
      latest_state: "active",
      updated_at: at,
      updated_by: "approver@veyra",
    },
    versions: [activeVersion(id, yaml, author, "approver@veyra", at)],
    history: [
      { version: 1, action: "submitted", from_state: null, to_state: "canary", actor: author, at, reason: "" },
      { version: 1, action: "approved", from_state: "canary", to_state: "canary", actor: "approver@veyra", at, reason: "" },
      { version: 1, action: "promoted", from_state: "canary", to_state: "active", actor: "approver@veyra", at, reason: "" },
    ],
  };
}

export function mockContracts(): MockContract[] {
  return [
    activeContract("acme_ngfw_cef", "t_ntro_core", "src_fw_dmz_01",
      libraryYaml("acme_ngfw_cef", "src_fw_dmz_01", "CEF:0|Acme|NGFW|<version>|<sig>|<name>|<sev:int>|<ext:kv>"),
      "admin@veyra", "2026-09-26T06:00:00.000000000Z"),
    activeContract("authsrv", "t_maha_power", "src_authsrv_01", AUTHSRV_V1_YAML, "author@maha",
      "2026-09-26T08:32:40.000000000Z"),
    activeContract("linux_sshd", "t_ntro_core", "src_lnx_core_07",
      libraryYaml("linux_sshd", "src_lnx_core_07", "Failed password for <user> from <src_ip:ip> port <src_port:int> ssh2"),
      "admin@veyra", "2026-09-26T06:00:00.000000000Z"),
  ];
}

export const T1_TEXT =
  '<134>Sep 26 14:05:09 fw01 app[233]: {"evt":"auth","msg":"user=r.patil OK login from 10.4.1.20 via 10.2.3.4"} | trace=';
export const T2_TEXT =
  '<134>Sep 26 14:05:10 fw01 app[233]: {"evt":"session","msg":"session 7781 closed for r.patil after 312s"}';
const T1_SIG = "t_5e2f0a1c9d41";
const T2_SIG = "t_8b7c6d5e4f30";
const in1 = (needle: string) => T1_TEXT.indexOf(needle);
const in2 = (needle: string) => T2_TEXT.indexOf(needle);

const ONBOARD_REQUEST = {
  allowed_classes: { authentication: ["logoff", "logon"] },
  allowed_fields: ["dst_endpoint.ip", "message", "src_endpoint.ip", "status_id", "user.name"],
  enums: { status_id: { "0": "Unknown", "1": "Success", "2": "Failure", "99": "Other" } },
};

/** The ready onboarding draft for T1 and T2 (Beat 2), keyed `dr_onboard`. */
export function onboardDraft(sourceId: string, tenantId: string): Draft {
  return {
    draft_id: "dr_onboard",
    tenant_id: tenantId,
    source_id: sourceId,
    drift_id: null,
    contract_id: contractIdFor(sourceId),
    state: "ready",
    detail: "",
    templates: [
      {
        template_sig: T1_SIG,
        drain_template: "user=<*> OK login from <*> via <*>",
        request: {
          ...ONBOARD_REQUEST,
          tokens: [
            { id: "k2", value: "<USER_1>", kind: "user", key: "user" },
            { id: "k6", value: "10.4.1.20", kind: "ip" },
            { id: "k8", value: "10.2.3.4", kind: "ip" },
          ],
        },
        response: {
          class: "authentication",
          activity: "logon",
          confidence: "high",
          mappings: [
            { ocsf_path: "user.name", token: "k2" },
            { ocsf_path: "src_endpoint.ip", token: "k6" },
            { ocsf_path: "dst_endpoint.ip", token: "k8" },
            { ocsf_path: "status_id", const: 1 },
          ],
          rationale: "a successful login: user, source and relay",
        },
        source: "cache:qwen2.5:3b",
        latency_ms: 38,
        review: [],
        notes: [],
        template: {
          id: "auth_ok",
          pattern: "user=<user> OK login from <src_ip:ip> via <dst_ip:ip>",
          class: "authentication",
          activity: "logon",
          map: { "user.name": "$user", "src_endpoint.ip": "$src_ip", "dst_endpoint.ip": "$dst_ip", status_id: { const: 1 } },
        },
        sample_text: T1_TEXT,
        spans: [
          { id: "k2", value: "r.patil", kind: "user", start: in1("r.patil"), end: in1("r.patil") + 7 },
          { id: "k6", value: "10.4.1.20", kind: "ip", start: in1("10.4.1.20"), end: in1("10.4.1.20") + 9 },
          { id: "k8", value: "10.2.3.4", kind: "ip", start: in1("10.2.3.4"), end: in1("10.2.3.4") + 8 },
        ],
      },
      {
        template_sig: T2_SIG,
        drain_template: "session <*> closed for <*> after <*>",
        request: {
          ...ONBOARD_REQUEST,
          tokens: [
            { id: "k1", value: "7781", kind: "int" },
            { id: "k4", value: "<USER_1>", kind: "user" },
            { id: "k6", value: "312s", kind: "duration" },
          ],
        },
        response: {
          class: "authentication",
          activity: "logoff",
          confidence: "medium",
          mappings: [
            { ocsf_path: "user.name", token: "k4" },
            { ocsf_path: "status_id", const: 1 },
          ],
          rationale: "a session end is a logoff for that user",
        },
        source: "cache:qwen2.5:3b",
        latency_ms: 41,
        review: [],
        notes: ["session id and duration stay unmapped"],
        template: {
          id: "session_closed",
          pattern: "session <session_id:int> closed for <user> after <seconds:int>s",
          class: "authentication",
          activity: "logoff",
          map: { "user.name": "$user", status_id: { const: 1 } },
          unmapped: ["session_id", "seconds"],
        },
        sample_text: T2_TEXT,
        spans: [
          { id: "k1", value: "7781", kind: "int", start: in2("7781"), end: in2("7781") + 4 },
          { id: "k4", value: "r.patil", kind: "user", start: in2("r.patil"), end: in2("r.patil") + 7 },
          { id: "k6", value: "312", kind: "int", start: in2("312s"), end: in2("312s") + 3 },
        ],
      },
    ],
    yaml: AUTHSRV_V1_YAML.replaceAll("authsrv", contractIdFor(sourceId)).replace("t_maha_power", tenantId),
    verification: {
      ok: true,
      compile_error: null,
      tiers: { "1": 10 },
      provenance: [
        { ocsf_path: "dst_endpoint.ip", ok: true, reason: "", kind: "located" },
        { ocsf_path: "src_endpoint.ip", ok: true, reason: "", kind: "located" },
        { ocsf_path: "status_id", ok: true, reason: "derived: const", kind: "derived" },
        { ocsf_path: "user.name", ok: true, reason: "", kind: "located" },
      ],
      type_issues: [],
    },
    backtest: null,
    library: [
      { contract_id: "linux_sshd", path: "library/linux_sshd.yaml", tier1_pct: 0, tier2_pct: 0, matched: false },
    ],
    created_by: "author@maha",
    created_at: "2026-09-26T08:32:30.000000000Z",
    updated_at: "2026-09-26T08:32:31.000000000Z",
  };
}

/** How many pasted lines fit a Drain template (`<*>` is one token), as the analysis counts a shape. */
function linesMatching(drainTemplate: string, samples: readonly string[]): number {
  const escaped = drainTemplate.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replaceAll("<\\*>", "\\S+");
  const pattern = new RegExp(escaped);
  return samples.flatMap((s) => s.split(/\r?\n/)).filter((line) => pattern.test(line)).length;
}

/** The analyze stream for T1 + T2 (Plan 5 Task 7's frames, in order). */
export function analyzeFrames(sourceId: string, samples: readonly string[] = []): { event: string; data: unknown }[] {
  const draft = onboardDraft(sourceId, "t_maha_power");
  return [
    {
      event: "classification",
      data: {
        // classify() returns contract-envelope layers (veyra_contracts.drafting.classify).
        layers: [{ syslog: { variant: "auto" } }, { json: { text_field: "msg" } }],
        contract_id: contractIdFor(sourceId),
      },
    },
    {
      event: "templates",
      data: draft.templates.map((t) => ({
        template_sig: t.template_sig,
        drain_template: t.drain_template,
        count: linesMatching(t.drain_template, samples),
      })),
    },
    { event: "library", data: { matches: draft.library, matched: null } },
    ...draft.templates.map((t) => ({
      event: "draft",
      data: { template_sig: t.template_sig, source: t.source, pattern: t.template.pattern, latency_ms: t.latency_ms },
    })),
    { event: "done", data: { draft_id: draft.draft_id, verification: draft.verification } },
  ];
}

export const AUDIT: AuditRow[] = [
  { actor: "approver@veyra", role: "pack_approver", action: "contract.promote", target: "authsrv@1", detail: "", at: "2026-09-26T08:32:40.000000000Z" },
  { actor: "approver@veyra", role: "pack_approver", action: "contract.approve", target: "authsrv@1", detail: "", at: "2026-09-26T08:32:38.000000000Z" },
  { actor: "author@maha", role: "pack_author", action: "contract.submit", target: "authsrv@1", detail: "", at: "2026-09-26T08:32:33.000000000Z" },
  { actor: "author@maha", role: "pack_author", action: "key.create", target: "k_2f81", detail: "source=src_authsrv_01", at: "2026-09-26T08:32:45.000000000Z" },
  { actor: "author@maha", role: "pack_author", action: "source.create", target: "src_authsrv_01", detail: "", at: "2026-09-26T08:32:10.000000000Z" },
];

/** services/control_api/seed/routes.yaml (IF-ROUTES). */
export const ROUTES: RouteSpec[] = [
  {
    id: "wazuh_main",
    filter: { tenants: ["*"], tiers: [1, 2, 3, 4] },
    format: "ocsf_json",
    masking: "none",
    sink: { type: "ndjson_file", path: "/sinks/wazuh/veyra.ndjson", fsync_ms: 200 },
  },
  {
    id: "partner_masked",
    filter: { tenants: ["t_maha_power"], classes: [3002, 4001] },
    format: "ocsf_json",
    masking: { "user.name": "hmac", "src_endpoint.ip": "hmac", raw_data: "redact" },
    sink: { type: "ndjson_file", path: "/sinks/partner/partner.ndjson" },
  },
];

export const T3_SIG = "t_3c85a1bfbf81";
export const T3_TEXT =
  '<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1"} | trace=';
const at = (needle: string) => T3_TEXT.indexOf(needle);

export const DRIFT: DriftItem[] = [
  {
    drift_id: "dr_item_t3",
    source_id: "src_authsrv_01",
    tenant_id: "t_maha_power",
    template_sig: T3_SIG,
    related_sigs: [],
    drain_template: "user=<*> FAILED login from <*> via <*> attempts:<*>",
    count: 8,
    first_seen: "2026-09-26T08:35:11.000000000Z",
    last_seen: "2026-09-26T08:35:19.000000000Z",
    samples_masked: ["user=<USER_1> FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1"],
    sample_event_uids: ["0192a4f0-0000-7000-8000-000000000000"],
    state: "draft_ready",
    draft_id: "dr_t3",
    resolved_by: null,
    updated_at: "2026-09-26T08:35:20.000000000Z",
  },
];

export const DRAFT_T3: Draft = {
  draft_id: "dr_t3",
  tenant_id: "t_maha_power",
  source_id: "src_authsrv_01",
  drift_id: "dr_item_t3",
  contract_id: "authsrv",
  state: "ready",
  detail: "",
  templates: [
    {
      template_sig: T3_SIG,
      drain_template: "user=<*> FAILED login from <*> via <*> attempts:<*>",
      request: {
        tokens: [
          { id: "k2", value: "<USER_1>", kind: "user", key: "user" },
          { id: "k6", value: "103.21.4.77", kind: "ip" },
          { id: "k8", value: "10.2.3.4", kind: "ip" },
          { id: "k10", value: "1", kind: "int" },
        ],
        allowed_classes: { authentication: ["logoff", "logon"], network_activity: ["close", "open", "refuse", "traffic"] },
        allowed_fields: ["dst_endpoint.ip", "message", "severity_id", "src_endpoint.ip", "status_id", "user.name"],
        enums: { status_id: { "0": "Unknown", "1": "Success", "2": "Failure", "99": "Other" } },
      },
      response: {
        class: "authentication",
        activity: "logon",
        confidence: "high",
        mappings: [
          { ocsf_path: "user.name", token: "k2" },
          { ocsf_path: "src_endpoint.ip", token: "k6" },
          { ocsf_path: "dst_endpoint.ip", token: "k8" },
          { ocsf_path: "status_id", const: 2 },
        ],
        rationale: "user, source and relay; FAILED is a failure",
      },
      source: "cache:qwen2.5:3b",
      latency_ms: 42,
      review: [],
      notes: [],
      template: {
        id: "logon_failed",
        pattern: "user=<user> FAILED login from <src_ip:ip> via <dst_ip:ip> attempts:<attempts:int>",
        class: "authentication",
        activity: "logon",
        map: { "user.name": "$user", "src_endpoint.ip": "$src_ip", "dst_endpoint.ip": "$dst_ip", status_id: { const: 2 } },
        unmapped: ["attempts"],
      },
      sample_text: T3_TEXT,
      spans: [
        { id: "k2", value: "a.sharma", kind: "user", start: at("a.sharma"), end: at("a.sharma") + 8 },
        { id: "k6", value: "103.21.4.77", kind: "ip", start: at("103.21.4.77"), end: at("103.21.4.77") + 11 },
        { id: "k8", value: "10.2.3.4", kind: "ip", start: at("10.2.3.4"), end: at("10.2.3.4") + 8 },
        { id: "k10", value: "1", kind: "int", start: at("attempts:1") + 9, end: at("attempts:1") + 10 },
      ],
    },
  ],
  yaml: AUTHSRV_V2_YAML,
  verification: {
    ok: true,
    compile_error: null,
    tiers: { "1": 8 },
    provenance: [
      { ocsf_path: "dst_endpoint.ip", ok: true, reason: "", kind: "located" },
      { ocsf_path: "message", ok: true, reason: "derived: text", kind: "derived" },
      { ocsf_path: "src_endpoint.ip", ok: true, reason: "", kind: "located" },
      { ocsf_path: "status_id", ok: true, reason: "derived: const", kind: "derived" },
      { ocsf_path: "user.name", ok: true, reason: "", kind: "located" },
    ],
    type_issues: [],
  },
  backtest: {
    n: 8, tier_before: { "4": 8 }, tier_after: { "1": 8 }, upgraded: 8, regressed: 0, unchanged: 0,
    examples: [], sigs: [T3_SIG], samples: 0, events_found: 8, raw_missing: 0, error: null, ms: 38,
  },
  library: [],
  created_by: "author@maha",
  created_at: "2026-09-26T08:35:20.000000000Z",
  updated_at: "2026-09-26T08:35:21.000000000Z",
};
