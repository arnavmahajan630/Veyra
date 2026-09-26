# A1 — Edge collectors (Vector): stamping, framing, source resolution

```
track: A   owner: A   status: todo
contracts: v1.1
depends_on: [S0]                  unblocks: [CP1, B2, B1]
consumes: [IF-INVENTORY, IF-TOPICS, IF-PORTS, IF-NAMING]
provides: [IF-ENVELOPE (edge producers)]
directories: [edge/, compose/ (edge services only)]
```

## Goal

Two Vector instances (`edge-dmz`, `edge-core`) that receive syslog over UDP and TCP, frame events correctly (including multi-line), stamp the evidence fields **before any parsing**, resolve the source from the inventory, and publish IF-ENVELOPE to `raw.<vendor>` with durable settings and a disk buffer.

v1 refs: §5 Zone Edge, §6 steps 1–4, §7.1–7.3, §13 (edge failure). Demo beats 1–3 depend on it.

## Design

**Config generation.** `edge/vector/vector.toml.tmpl` is rendered per zone by `edge/render.py` into `vector-dmz.toml` and `vector-core.toml`. Listeners come from IF-PORTS.

**Sources:**
- `socket` UDP, `mode=udp`, `decoding.codec=bytes`. The whole datagram is one event (`framing.method="datagram"`).
- `socket` TCP, `mode=tcp`, newline-delimited, with `max_length=${VEYRA_MAX_EVENT_BYTES}`. Configure `host_key` and `port_key` so the peer address is available.

**Multi-line join (TCP).** A `reduce` transform grouped by `(host, port)`. A line starting with whitespace (`^\s`) or `at ` is a continuation of the previous event. Flush on the next non-continuation line or after 500 ms (`VEYRA_EDGE_MULTILINE_FLUSH_MS`). Joined events use `framing.method="multiline_join"` and `parts=n`. The joined bytes are the original lines joined with `\n`, exactly as received.

**Stamping (`remap`, VRL), in this order:**
1. `raw = .message` (bytes); `.raw_len = strlen(raw)`.
2. `.truncated = raw_len >= max_len`.
3. `.raw_sha256 = sha2(raw, variant: "SHA-256")`, checking the output format is lowercase hex.
4. `.raw_b64 = encode_base64(raw)`.
5. `.event_uid = uuid_v7()`. If the pinned VRL lacks it, use `uuid_v4()` and log a DEVIATION; the normalizer must not rely on time ordering of UIDs.
6. `.received_time = format_timestamp!(now(), "%+")` with nanoseconds.
7. `collector_id`, `zone`, `listener` and `transport` are constants per source block.

**Resolution.**
- Extract the syslog host from the first 256 bytes with `parse_regex` (3164/5424 variants per IF-INVENTORY).
- Lookup 1: `get_enrichment_table_record("sources", {"listener":…, "match_kind":"peer_ip", "match_value": peer_ip})`.
- Lookup 2: the same with `match_kind:"syslog_host"`.
- Else: unregistered defaults.
- `vendor` sets the topic.

**Output.**
- Build the IF-ENVELOPE object explicitly (no extra Vector metadata leaks) and encode it as JSON.
- `kafka` sink:
  - `topic = "raw.{{ vendor }}"`, `key_field = "source_id"`;
  - librdkafka: `acks=all`, `enable.idempotence=true`, `compression.codec=zstd`;
  - `buffer.type="disk"`, `max_size=${VEYRA_EDGE_BUFFER_BYTES}`, `when_full="block"`.

**Inventory reload.** Verify the IF-INVENTORY mechanism: `--watch-config` plus touching `reload.stamp` included in the config. Document the verified mechanism in `02_CONTRACTS.md` IF-INVENTORY (a clarification or additive change). If hot reload of enrichment tables doesn't work, C1 restarts the edge container via the Docker API after an inventory change (acceptable: takes < 2 s). Record the choice.

**Network.** The compose network `veyra_net` uses a fixed subnet, so demo senders can have static IPs if needed.

## Tasks
- [ ] 1. Template plus render script; two edge services in compose; health via Vector's API `/health`.
- [ ] 2. UDP and TCP sources; the multi-line `reduce` for TCP.
- [ ] 3. VRL stamping and envelope build; Vector unit tests (`[[tests]]` in config) for stamping and resolution.
- [ ] 4. Enrichment table from `edge/vector/inventory/sources.csv` (seeded in S0 with the NTRO sources); both lookups.
- [ ] 5. Kafka sink with durability settings and a disk buffer.
- [ ] 6. Verify the inventory reload mechanism; update IF-INVENTORY.
- [ ] 7. Integration test `tests/int/test_edge.py`: send corpus lines via Python sockets (UDP and TCP), consume `raw.*`, validate with `veyra_common` models, recompute `sha256(b64decode(raw_b64)) == raw_sha256`, and check multi-line joins and unregistered routing.
- [ ] 8. **Parity test vector:** the same raw bytes through the edge and through `veyra_common.hashing` give the same `raw_sha256` and `raw_b64`. Record 3 vectors in `packages/veyra_common/fixtures/envelope_vectors.json` (the gateway will reuse them).

## Acceptance criteria
- [ ] AC1: 1000 UDP + 1000 TCP lines at 200 EPS → 2000 valid envelopes, 0 invalid; all sha checks pass.
- [ ] AC2: The T3 line with a stack trace over TCP → **one** envelope, `framing.method="multiline_join"`, `parts=2`, and the bytes equal the original two lines joined with `\n`.
- [ ] AC3: Stop Kafka for 60 s while sending; restart it → all events arrive (disk buffer), no duplicates by `event_uid`.
- [ ] AC4: Add a row to `sources.csv` → within 5 s, new events from that host resolve to the new `source_id`.
- [ ] AC5: Unknown host → `raw.unregistered`, with `source_id=unregistered`.

## Settings
`VEYRA_MAX_EVENT_BYTES`, `VEYRA_EDGE_MULTILINE_FLUSH_MS` (500), `VEYRA_EDGE_BUFFER_BYTES` (268435456 on laptop).

## Risks and fallbacks

| Risk | Fallback |
|---|---|
| VRL function gaps in the pinned Vector | Pin a newer Vector, or move the missing function into a tiny Python "edge-stamp" sidecar that reads Vector's output (last resort; log a DEVIATION) |
| UDP datagrams > MTU truncated by the kernel | Document it; `truncated=true` when `len == max` |

## Implementation notes
_(filled after execution)_
