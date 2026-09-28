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
