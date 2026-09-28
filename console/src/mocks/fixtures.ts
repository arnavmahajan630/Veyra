// The pre-demo world of 04_DEMO_SCRIPT §2, as the APIs would return it.
import type { ApiKeyRow, Me, Overview, OverviewSource, Source, SourceHealth, Tenant, TierCounts } from "../api/types";

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

export const KEYS: Record<string, ApiKeyRow[]> = { src_fw_dmz_01: [], src_lnx_core_07: [] };

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
