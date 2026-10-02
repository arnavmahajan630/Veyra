// Mock-mode fixtures for the Lineage and Evidence pages.
//
// Byte offsets are *computed* from the raw text with TextEncoder rather than written by
// hand. Hand-counted spans are what let the pane look right while being wrong, and they
// are impossible to keep correct across a multi-byte or JSON-escaped value.

import type { EventDetail, LedgerRoot, VerifyReport } from "../api/types";

const encoder = new TextEncoder();

/** The [start, end) UTF-8 byte span of `value` inside `text`, from `from` onwards. */
export function spanOf(text: string, value: string, from = 0): [number, number] {
  const index = text.indexOf(value, from);
  if (index === -1) throw new Error(`fixture bug: ${JSON.stringify(value)} is not in the raw text`);
  const start = encoder.encode(text.slice(0, index)).length;
  return [start, start + encoder.encode(value).length];
}

export function byteLength(text: string): number {
  return encoder.encode(text).length;
}

// ---------------------------------------------------------------- the T3 auth event
// The Beat 5 event: multi-line, with a JSON-escaped body inside a syslog frame.
export const AUTH_UID = "0192a4f0-0000-7000-8000-000000000001";
export const AUTH_RAW =
  '<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login from ' +
  '103.21.4.77 via 10.2.3.4 attempts:1"} | trace=\n  at com.x.Auth.login(Auth.java:88)';

const authOffsets: Record<string, [number, number]> = {
  "user.name": spanOf(AUTH_RAW, "a.sharma"),
  "src_endpoint.ip": spanOf(AUTH_RAW, "103.21.4.77"),
  "dst_endpoint.ip": spanOf(AUTH_RAW, "10.2.3.4"),
};

// ---------------------------------------------------------------- the OT historian event
// Devanagari values in a semicolon format from an unregistered host, so it lands tier 3.
// This is the multi-byte case AC1 names.
export const OT_UID = "0192a4f0-0000-7000-8000-0000000000ot".replace("ot", "02");
export const OT_RAW =
  "26-09-2026 14:05:13;ot-hist-01;संयंत्र=बॉयलर-2;तापमान=412;स्थिति=चेतावनी;उपयोगकर्ता=r.deshmukh";

const otOffsets: Record<string, [number, number]> = {
  "observables.hostname_1": spanOf(OT_RAW, "ot-hist-01"),
  "observables.user_1": spanOf(OT_RAW, "r.deshmukh"),
  // A Devanagari value: three bytes per code point, so a byte span is not a char span.
  "unmapped.स्थिति": spanOf(OT_RAW, "चेतावनी"),
};

function rawInfo(text: string, overrides: Partial<EventDetail["raw"]> = {}) {
  return {
    raw_ref: { topic: "raw.custom", partition: 0, offset: 14 },
    raw_sha256: "42d8c366914595e865f58197779f67a6d8febe771c5ec8ad7dd1e6e9ab577131",
    raw_len: byteLength(text),
    received_time: "2026-09-26T14:05:11.000Z",
    tenant_id: "t_maha_power",
    source_id: "src_authsrv_01",
    vendor: "custom",
    zone: "dmz",
    collector_id: "gw_01",
    transport: "http_push",
    custody: "realtime",
    auth_method: "token",
    framing_method: "multiline_join",
    framing_truncated: false,
    framing_parts: 2,
    raw_preview: text.slice(0, 80).replace("\n", " "),
    raw_text: text,
    raw_b64: btoa(String.fromCharCode(...encoder.encode(text))),
    ...overrides,
  } as EventDetail["raw"];
}

export const AUTH_EVENT: EventDetail = {
  event_uid: AUTH_UID,
  raw: rawInfo(AUTH_RAW),
  index_available: true,
  revisions: [
    {
      revision: 1,
      tier: 3,
      conformance: "unknown_template",
      contract_ref: null,
      template_sig: "t_unknown_0001",
      class_uid: 0,
      category: "uncategorized",
      norm_topic: "norm.uncategorized",
      produced_at: "2026-09-26T14:05:11.200Z",
      replay: false,
      // Tier 3 locates observables but maps no OCSF fields.
      ocsf: {
        observables: [
          { name: "ip_1", type: "ip", value: "103.21.4.77" },
          { name: "user_1", type: "user", value: "a.sharma" },
        ],
      },
      field_offsets: {
        "observables.ip_1": spanOf(AUTH_RAW, "103.21.4.77"),
        "observables.user_1": spanOf(AUTH_RAW, "a.sharma"),
      },
      derived_fields: {},
    },
    {
      revision: 2,
      tier: 1,
      conformance: "match",
      contract_ref: "authsrv@2",
      template_sig: "t_a1b2c3d4e5f6",
      template_id: "auth_failed",
      class_uid: 3002,
      category: "iam",
      norm_topic: "norm.iam",
      produced_at: "2026-09-26T14:06:00.000Z",
      replay: true,
      replay_job_id: "j_0192a4f0",
      ocsf: {
        class_uid: 3002,
        category_uid: 3,
        user: { name: "a.sharma" },
        src_endpoint: { ip: "103.21.4.77" },
        dst_endpoint: { ip: "10.2.3.4" },
        status_id: 2,
        activity_name: "Logon",
      },
      field_offsets: authOffsets,
      // status_id is a constant and activity_name a vocabulary lookup: neither is in the
      // raw bytes, and both must say so rather than show an unexplained tick.
      derived_fields: { status_id: "const", activity_name: "vocab:activity" },
    },
  ],
  vault: {
    segment_id: "seg_raw.custom_1_00000000000000000014",
    record_idx: 14,
    chain_hash: "3a8c1f9e2b4d6c8a0f2e4b6d8c0e2a4f",
    sealed: true,
    sealed_at: "2026-09-26T14:05:31.000Z",
    window_id: "w_1790000060",
  },
  receipts: [
    { revision: 1, route_id: "wazuh_main", status: "delivered", detail: "tier 3 delivered", at: "2026-09-26T14:05:12.000Z" },
    { revision: 2, route_id: "wazuh_main", status: "delivered", detail: "wazuh alert 100111", at: "2026-09-26T14:06:00.000Z" },
    { revision: 2, route_id: "partner_masked", status: "delivered", detail: "partner HMAC feed", at: "2026-09-26T14:06:01.000Z" },
  ],
  dlq: [],
  shadow: [],
};

export const OT_EVENT: EventDetail = {
  event_uid: OT_UID,
  raw: rawInfo(OT_RAW, {
    source_id: "",
    tenant_id: "t_ntro_core",
    vendor: "unknown",
    zone: "core",
    transport: "syslog_udp",
    framing_method: "newline",
    framing_parts: 1,
  }),
  index_available: true,
  revisions: [
    {
      revision: 1,
      tier: 3,
      conformance: "unknown_template",
      contract_ref: null,
      template_sig: "t_ot_hist_0001",
      class_uid: 0,
      category: "uncategorized",
      norm_topic: "norm.uncategorized",
      produced_at: "2026-09-26T14:05:13.400Z",
      replay: false,
      ocsf: {
        observables: [
          { name: "hostname_1", type: "hostname", value: "ot-hist-01" },
          { name: "user_1", type: "user", value: "r.deshmukh" },
        ],
        unmapped: { "स्थिति": "चेतावनी", "तापमान": "412" },
      },
      field_offsets: otOffsets,
      derived_fields: {},
    },
  ],
  vault: {
    segment_id: "seg_raw.custom_0_00000000000000000009",
    record_idx: 9,
    chain_hash: "9f1e2d3c4b5a6978",
    sealed: true,
    sealed_at: "2026-09-26T14:05:33.000Z",
    window_id: "w_1790000060",
  },
  receipts: [
    { revision: 1, route_id: "wazuh_main", status: "delivered", detail: "tier 3 delivered", at: "2026-09-26T14:05:14.000Z" },
  ],
  dlq: [],
  shadow: [],
};

export const EVENTS: Record<string, EventDetail> = {
  [AUTH_UID]: AUTH_EVENT,
  [OT_UID]: OT_EVENT,
};

// ---------------------------------------------------------------- search
export interface SearchHitFixture {
  event_uid: string;
  tenant_id: string;
  source_id: string;
  received_time: string;
  revision: number;
  tier: number;
  conformance: string;
  template_sig: string;
  contract_ref: string | null;
  raw_sha256: string;
  raw_preview: string;
  /** Terms this hit matches on; mock search filters against them. */
  terms: string[];
}

export const SEARCH_HITS: SearchHitFixture[] = [
  {
    event_uid: AUTH_UID,
    tenant_id: "t_maha_power",
    source_id: "src_authsrv_01",
    received_time: "2026-09-26T14:05:11.000Z",
    revision: 2,
    tier: 1,
    conformance: "match",
    template_sig: "t_a1b2c3d4e5f6",
    contract_ref: "authsrv@2",
    raw_sha256: "42d8c366914595e865f58197779f67a6d8febe771c5ec8ad7dd1e6e9ab577131",
    raw_preview: AUTH_RAW.slice(35, 120),
    terms: [AUTH_UID, "a.sharma", "103.21.4.77", "10.2.3.4", "authsrv", "t_a1b2c3d4e5f6", "42d8c366"],
  },
  {
    event_uid: OT_UID,
    tenant_id: "t_ntro_core",
    source_id: "",
    received_time: "2026-09-26T14:05:13.000Z",
    revision: 1,
    tier: 3,
    conformance: "unknown_template",
    template_sig: "t_ot_hist_0001",
    contract_ref: null,
    raw_sha256: "77aa11bb22cc33dd44ee55ff6677889900aabbccddeeff001122334455667788",
    raw_preview: OT_RAW.slice(0, 80),
    terms: [OT_UID, "ot-hist-01", "r.deshmukh", "चेतावनी", "t_ot_hist_0001"],
  },
];

export function searchFixture(q: string) {
  const needle = q.trim().toLowerCase();
  const hits = needle
    ? SEARCH_HITS.filter((hit) => hit.terms.some((term) => term.toLowerCase().includes(needle)))
    : [];
  const matched = !needle
    ? null
    : hits.some((h) => h.event_uid.toLowerCase() === needle)
      ? "event_uid"
      : hits.some((h) => h.raw_sha256.toLowerCase().startsWith(needle))
        ? "sha256_prefix"
        : hits.some((h) => h.template_sig.toLowerCase() === needle)
          ? "template_sig"
          : "search_terms";
  return { q, matched_on: hits.length ? matched : null, hits: hits.map(({ terms, ...hit }) => hit) };
}

// ---------------------------------------------------------------- verify
const LABELS: Record<string, string> = {
  fetch_raw: "Raw bytes fetched from the sealed segment",
  decrypt_segment: "Segment decrypted (AES-256-GCM) and blob hash matches",
  hash_raw: "SHA-256 of the raw bytes matches the envelope",
  chain_walk: "Per-partition hash chain recomputes",
  segment_digest: "Segment digest recomputes from the header",
  merkle_inclusion: "Segment is included under the signed window root",
  root_signature: "Window root carries a valid Ed25519 signature",
  immudb_verified: "Root anchored in immudb",
};

const TIMINGS: Record<string, number> = {
  fetch_raw: 4.2,
  decrypt_segment: 8.1,
  hash_raw: 1.5,
  chain_walk: 12.4,
  segment_digest: 3.2,
  merkle_inclusion: 2.8,
  root_signature: 6.5,
  immudb_verified: 0,
};

function step(id: string, ok: boolean, detail: string, status?: string) {
  return { id, label: LABELS[id] ?? id, ok, detail, ms: TIMINGS[id] ?? 1, ...(status ? { status } : {}) };
}

/** An honest 8-step report: seven green, immudb grey (a prototype in this build). */
export function verifyOk(uid: string): VerifyReport {
  return {
    event_uid: uid,
    verified: true,
    steps: [
      step("fetch_raw", true, `${byteLength(AUTH_RAW)} bytes read`),
      step("decrypt_segment", true, "auth tag matches"),
      step("hash_raw", true, "42d8c366… matches"),
      step("chain_walk", true, "recomputes to the segment head"),
      step("segment_digest", true, "digest recomputes from the header"),
      step("merkle_inclusion", true, "leaf 3 of 8 under root b4c81a29…"),
      step("root_signature", true, "w_1790000060 signed by k_ed25519_1"),
      step("immudb_verified", false, "not implemented in this prototype: the root is in the local ledger only", "not_implemented"),
    ],
  };
}

/**
 * After an insider rewrite: the stored bytes changed, so the ingest-time hash and the
 * Merkle leaf no longer match — while the signed root still verifies. That contrast is
 * the whole point of Beat 5, so the mock has to reproduce it exactly.
 */
export function verifyTampered(uid: string): VerifyReport {
  return {
    event_uid: uid,
    verified: false,
    steps: [
      step("fetch_raw", true, `${byteLength(AUTH_RAW)} bytes read`),
      step("decrypt_segment", true, "auth tag matches: the segment was re-encrypted with the stolen key"),
      // An insider rewrite re-seals the segment, so every check *inside* it still passes —
      // including hash_raw, which they recompute. Only the leaf under the signed root does
      // not match, and that is what `tests/tamper/test_tamper_matrix.py` asserts.
      step("hash_raw", true, "SHA-256 recomputed by the insider: it matches the rewritten bytes"),
      step("chain_walk", true, "the record's chain hash recomputes: the insider fixed it too"),
      step("segment_digest", true, "segment seg_raw.custom_1_…14 is internally consistent after re-sealing"),
      step("merkle_inclusion", false, "segment seg_raw.custom_1_…14 was altered after it was sealed at 14:05:31: the recomputed leaf is not under root b4c81a29…"),
      step("root_signature", true, "w_1790000060 signed by k_ed25519_1"),
      step("immudb_verified", false, "not implemented in this prototype: the root is in the local ledger only", "not_implemented"),
    ],
  };
}

/** Sealed, but its window has not been signed yet: grey, not red. */
export function verifyPending(uid: string): VerifyReport {
  return {
    event_uid: uid,
    verified: false,
    steps: [
      step("fetch_raw", true, "84 bytes read"),
      step("decrypt_segment", true, "auth tag matches"),
      step("hash_raw", true, "77aa11bb… matches"),
      step("chain_walk", true, "recomputes to the segment head"),
      step("segment_digest", true, "digest recomputes from the header"),
      step("merkle_inclusion", false, "the window holding this segment has not been signed yet (sealing in <= 60 s)", "pending_seal"),
      step("root_signature", false, "no signed root covers this segment yet (sealing in <= 60 s)", "pending_seal"),
      step("immudb_verified", false, "not implemented in this prototype: the root is in the local ledger only", "not_implemented"),
    ],
  };
}

// ---------------------------------------------------------------- roots
export function rootFixture(
  windowId: string,
  startUnix: number,
  leafCount: number,
  rootHex: string,
  audit: Partial<Pick<LedgerRoot, "signature_ok" | "prev_link_ok" | "window_order_ok" | "chain_ok">> = {},
): LedgerRoot {
  return {
    window_id: windowId,
    payload: {
      window_id: windowId,
      window_start: startUnix,
      window_end: startUnix + 60,
      leaf_count: leafCount,
      merkle_root_sha256: rootHex,
      key_id: "k_ed25519_1",
      alg: "Ed25519",
    },
    sig_b64: "dGVzdF9zaWduYXR1cmVfYmFzZTY0",
    payload_sha256: rootHex,
    signature_ok: true,
    prev_link_ok: true,
    window_order_ok: true,
    chain_ok: true,
    ...audit,
  };
}

export const ROOTS: LedgerRoot[] = [
  rootFixture("w_1790000060", 1790000060, 8, "b4c81a29384758d6e9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2"),
  rootFixture("w_1790000000", 1790000000, 6, "a1b2c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e"),
];
