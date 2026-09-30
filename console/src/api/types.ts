// Control-api types mirror C1's response models (IF-API-CONTROL). Lineage types are our
// reading of IF-API-EVIDENCE until B4 publishes its OpenAPI (decision TC14): the shapes
// below are sent to B as a changelog REQUEST so both sides agree before CP2.

export type Role = "admin" | "pack_author" | "pack_approver" | "org_viewer" | "auditor";
export const PLATFORM = "*";

export interface Me {
  user: { email: string; name: string };
  role: Role;
  tenant: string;
  demo_mode: boolean;
}

export interface Tenant {
  id: string;
  name: string;
  created_at: string;
}

export type SourceTransport = "syslog_udp" | "syslog_tcp" | "http_push";

export interface Source {
  id: string;
  tenant_id: string;
  name: string;
  vendor: string;
  zone: string;
  transport: SourceTransport;
  listener: string | null;
  match_kind: string | null;
  match_value: string | null;
  contract_id: string | null;
  expected_eps: number;
  salt_buckets: number;
  status: "active" | "paused";
  created_at: string;
}

export interface KeyCard {
  key_id: string;
  secret: string;
  endpoints: {
    hec_url: string;
    batch_url: string;
    syslog: { host: string; port: number | null; listener: string | null } | null;
  };
  curl_example: string;
}

export interface ApiKeyRow {
  key_id: string;
  source_id: string;
  status: "active" | "revoked";
  quota_eps: number;
  created_by: string;
  created_at: string;
  revoked_at: string | null;
}

export interface ControlEvent {
  type: "overview" | "drift" | "draft" | "contract" | "replay" | "source";
  data: Record<string, unknown>;
}

export type Tier = 1 | 2 | 3 | 4;
export type TierCounts = Record<"1" | "2" | "3" | "4", number>;

export interface OverviewSource {
  source_id: string;
  zone: string;
  eps: number;
  tiers: TierCounts;
  last_seen: string | null;
}

export interface OverviewRoute {
  route_id: string;
  delivered_per_min: number;
  failed_per_min: number;
  lag_s: number | null;
  breaker: "closed" | "open" | "half_open" | null;
}

export interface VaultStatus {
  segments: number;
  last_sealed_at: string | null;
  last_root: { window_id: string; window_end: string; immudb_verified: boolean } | null;
  chain_ok: boolean;
}

export interface Overview {
  eps_1m: number;
  totals_by_tier: TierCounts;
  sources: OverviewSource[];
  routes: OverviewRoute[];
  vault: VaultStatus;
  as_of: string;
  /**
   * Events per tier per second over the last 15 minutes, oldest first, ending at `as_of`.
   * Optional: the HTTP response should carry it so the tier bar opens full; SSE ticks may
   * omit it, and the console then adds one sample per tick.
   */
  tier_history?: TierCounts[];
}

export interface SourceHealth {
  source_id: string;
  tenant_id: string;
  zone: string;
  transport: string;
  contract_ref: string | null;
  expected_eps: number;
  actual_eps: number;
  last_seen: string | null;
  tiers: TierCounts;
  clock_skew_p50_ms: number | null;
}

// ---- C6: contracts, drift, drafts, replay, audit (Plans 3–5 response models)

export type ContractState = "draft" | "testing" | "canary" | "active" | "retired";

export interface VersionSummary {
  contract_id: string;
  version: number;
  state: ContractState;
  author: string;
  approved_by: string | null;
  approved_at: string | null;
  promoted_by: string | null;
  promoted_at: string | null;
  retired_reason: string | null;
  git_commit: string | null;
  created_at: string;
  draft_id: string | null;
}

export interface GoldenCase {
  sample: string;
  expect: string;
  ok: boolean;
  tier: number | null;
  diffs: string[];
  schema_errors: string[];
  error: string | null;
}

export interface BacktestExample {
  event_uid: string;
  before_tier: number;
  after_tier: number;
  changed_fields: string[];
  provenance_ok: boolean;
}

export interface BacktestResult {
  n: number;
  tier_before: Record<string, number>;
  tier_after: Record<string, number>;
  upgraded: number;
  regressed: number;
  unchanged: number;
  examples: BacktestExample[];
  sigs: string[];
  samples: number;
  events_found: number;
  raw_missing: number;
  error: string | null;
  ms: number;
}

export interface LintFinding {
  level: "error" | "warning";
  code: string;
  message: string;
  template: string | null;
}

export interface VersionDetail extends VersionSummary {
  yaml: string;
  compiled: Record<string, unknown> | null;
  golden: { passed: boolean; total: number; failed: number; cases: GoldenCase[] } | null;
  lint: LintFinding[];
  backtest: BacktestResult | null;
}

export interface Transition {
  version: number;
  action: string;
  from_state: string | null;
  to_state: string;
  actor: string;
  at: string;
  reason: string;
}

export interface ContractSummary {
  id: string;
  tenant_id: string;
  sources: string[];
  active_version: number | null;
  canary_version: number | null;
  latest_version: number;
  latest_state: ContractState;
  updated_at: string | null;
  updated_by: string | null;
}

export interface ContractDetail extends ContractSummary {
  versions: VersionSummary[];
  history: Transition[];
}

export interface TemplateChange {
  id: string;
  pattern_changed: boolean;
  class_changed: boolean;
  map_added: string[];
  map_removed: string[];
  map_changed: string[];
  unmapped_added: string[];
  unmapped_removed: string[];
}

export interface DiffOut {
  contract_id: string;
  from_version: number;
  to_version: number;
  semantic: {
    templates_added: string[];
    templates_removed: string[];
    templates_changed: TemplateChange[];
    order_changed: boolean;
    changed_sections: string[];
  };
  yaml: string;
}

export type DriftState = "open" | "drafting" | "draft_ready" | "resolved" | "dismissed";

export interface DriftItem {
  drift_id: string;
  source_id: string;
  tenant_id: string;
  template_sig: string;
  related_sigs: string[];
  drain_template: string;
  count: number;
  first_seen: string;
  last_seen: string;
  samples_masked: string[];
  sample_event_uids: string[];
  state: DriftState;
  draft_id: string | null;
  resolved_by: string | null;
  updated_at: string | null;
}

export interface DraftMapping {
  ocsf_path: string;
  token?: string;
  const?: number;
}

export interface DraftSpan {
  id: string;
  value: string;
  kind: string;
  start: number;
  end: number;
}

export interface DraftTemplate {
  template_sig: string;
  drain_template: string;
  request: {
    tokens: { id: string; value: string; kind: string; key?: string }[];
    allowed_classes: Record<string, string[]>;
    allowed_fields: string[];
    enums: Record<string, Record<string, string>>;
  };
  response: { class: string; activity: string; confidence: string; mappings: DraftMapping[]; rationale: string };
  source: string;
  latency_ms: number;
  review: string[];
  notes: string[];
  template: { id: string; pattern: string; class: string; activity: string; map: Record<string, unknown>; unmapped?: string[] };
  sample_text: string;
  spans: DraftSpan[];
}

export interface ProvenanceRow {
  ocsf_path: string;
  ok: boolean;
  reason: string;
  kind: "located" | "derived";
}

export interface Verification {
  ok: boolean;
  compile_error: { message: string; line: number | null; column: number | null } | null;
  tiers: Record<string, number>;
  provenance: ProvenanceRow[];
  type_issues: string[];
}

export interface LibraryMatch {
  contract_id: string;
  path: string;
  tier1_pct: number;
  tier2_pct: number;
  matched: boolean;
}

export interface Draft {
  draft_id: string;
  tenant_id: string;
  source_id: string | null;
  drift_id: string | null;
  contract_id: string | null;
  state: "drafting" | "ready" | "failed" | "submitted";
  detail: string;
  templates: DraftTemplate[];
  yaml: string;
  verification: Verification | null;
  backtest: BacktestResult | null;
  library: LibraryMatch[];
  created_by: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface DraftEdit {
  template_sig?: string;
  class?: string;
  activity?: string;
  mappings?: DraftMapping[];
}

export type ReplayState = "pending" | "publishing" | "normalizing" | "done" | "timed_out" | "failed";

export interface ReplayJob {
  job_id: string;
  contract_id: string;
  tenant_id: string;
  state: ReplayState;
  total: number;
  published: number;
  normalized: number;
  params: { template_sigs: string[]; source_id: string | null };
  created_by: string | null;
  created_at: string;
  finished_at: string | null;
  detail: string;
}

export interface AuditRow {
  actor: string;
  role: string;
  action: string;
  target: string;
  detail: string;
  at: string;
}

export interface RouteSpec {
  id: string;
  filter: Record<string, unknown>;
  format: string;
  masking: Record<string, string> | "none";
  sink: Record<string, unknown>;
}

export type AnalyzeEvent =
  | { event: "classification"; data: { layers: Record<string, unknown>[]; contract_id: string } }
  | { event: "templates"; data: { template_sig: string; drain_template: string; count: number }[] }
  | { event: "library"; data: { matches: LibraryMatch[]; matched: string | null } }
  | { event: "draft"; data: { template_sig: string; source: string; pattern: string; latency_ms: number } }
  | { event: "done"; data: { draft_id: string | null; library?: string; verification?: Verification } }
  | { event: "error"; data: { message: string } };

// ---------------------------------------------------------------- B6 Lineage & Evidence
export interface SearchHit {
  event_uid: string;
  tenant_id: string;
  source_id: string;
  received_time?: string | null;
  revision?: number | null;
  tier?: number | null;
  conformance?: string | null;
  template_sig?: string | null;
  contract_ref?: string | null;
  raw_sha256: string;
  raw_preview?: string | null;
}

export interface SearchResult {
  q: string;
  matched_on: string | null;
  hits: SearchHit[];
}

export interface RawInfo {
  raw_ref: { topic: string; partition: number; offset: number };
  raw_sha256: string;
  raw_len: number;
  received_time: string;
  tenant_id: string;
  source_id: string;
  vendor: string;
  zone: string;
  collector_id: string;
  transport: string;
  listener?: string | null;
  peer_ip?: string | null;
  custody: string;
  auth_method: string;
  framing_method: string;
  framing_truncated: boolean;
  framing_parts: number;
  raw_preview: string;
  /** The bytes themselves (IF-API-EVIDENCE); null when the segment cannot be read. */
  raw_text?: string | null;
  raw_b64?: string | null;
}

export interface EventRevision {
  revision: number;
  tier: number;
  conformance: string;
  contract_ref?: string | null;
  template_sig: string;
  template_id?: string | null;
  class_uid: number;
  category: string;
  norm_topic: string;
  produced_at: string;
  replay: boolean;
  replay_job_id?: string | null;
  search_terms?: string[];
  ocsf?: Record<string, unknown> | null;
  /** IF-ULPF: ocsf_path -> [start, end) byte offsets into the decoded raw bytes. */
  field_offsets?: Record<string, [number, number]>;
  /** IF-ULPF: ocsf_path -> "const" | "vocab:<name>" | "ts:<detail>" | "enrich" | "base64". */
  derived_fields?: Record<string, string>;
}

export interface VaultLocation {
  segment_id: string;
  record_idx: number;
  chain_hash: string;
  sealed: boolean;
  sealed_at: string;
  window_id?: string | null;
}

export interface ReceiptRow {
  revision: number;
  route_id: string;
  status: "delivered" | "filtered" | "failed";
  detail: string;
  at: string;
}

export interface EventDetail {
  event_uid: string;
  raw_ref?: { topic: string; partition: number; offset: number } | null;
  raw?: RawInfo | null;
  revisions: EventRevision[];
  vault?: VaultLocation | null;
  receipts: ReceiptRow[];
  dlq?: unknown[];
  shadow?: unknown[];
  /** False when the lineage index was unreachable and only the vault could answer. */
  index_available?: boolean;
}

export interface VerifyStep {
  id: string;
  label: string;
  ok: boolean;
  detail: string;
  ms: number;
  status?: string | null;
}

export interface VerifyReport {
  event_uid: string;
  verified: boolean;
  steps: VerifyStep[];
}

export interface LedgerRoot {
  window_id: string;
  payload: Record<string, unknown>;
  sig_b64: string;
  payload_sha256: string;
  /** Ledger-audit state (B3). Absent when the audit could not run. */
  signature_ok?: boolean;
  prev_link_ok?: boolean;
  window_order_ok?: boolean;
  chain_ok?: boolean;
}

export interface LedgerRootsResponse {
  count: number;
  ledger: string;
  roots: LedgerRoot[];
  audit_status?: "PASS" | "FAIL" | "unknown";
}

// ---------------------------------------------------------------- B7 Demo Engine
export interface DemoStageInfo {
  title: string;
  actions_count?: number;
  /** Human-readable labels of this stage's `expect` clauses. */
  expects?: string[];
  /** Onboarding sample lines this stage pre-fills (stage 2). */
  samples?: string[];
}

export interface DemoBaselineStream {
  name: string;
  via: string;
  corpus: string;
  eps: number;
}

export interface DemoScenario {
  scenario: string;
  seed?: number;
  baseline?: DemoBaselineStream[];
  stages: Record<string, DemoStageInfo>;
}

export interface ExpectOutcome {
  label: string;
  ok: boolean;
  detail: string;
  seconds: number;
}

export interface DemoStageStatus {
  stage: number;
  state: "idle" | "running" | "done" | "failed";
  /** label -> passed, for a quick lookup. */
  results?: Record<string, boolean>;
  /** The same outcomes with their detail and timing. */
  expects?: ExpectOutcome[];
  error?: string | null;
  all_states?: Record<string, string>;
}

export interface PreflightCheck {
  check: string;
  status: "PASS" | "WARN" | "FAIL";
  detail: string;
}

export interface ResetStep {
  name: string;
  ok: boolean;
  ms: number;
  detail?: string;
}

/** `GET /reset/status`: a reset runs in the background and is polled. */
export interface ResetStatus {
  running: boolean;
  ready: boolean;
  ok: boolean | null;
  seconds: number;
  over_budget: boolean;
  steps: ResetStep[];
}

export interface ResetStarted {
  started: boolean;
  budget_s: number;
}

export interface TamperResult {
  target?: string;
  mode?: string;
  detail?: string;
  verified?: boolean;
  failed_steps?: string[];
}

export interface ActiveTamper {
  mode: string;
  event_uid: string;
  at: string;
}

export interface TamperActive {
  active: ActiveTamper[];
  modes: string[];
}

export interface BaselineSnapshot {
  paused: boolean;
  target_eps: number;
  actual_eps: number;
  streams: Record<string, { sent: number; errors: number; eps: number }>;
}
