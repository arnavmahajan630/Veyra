# Shipping to an existing Wazuh (`WAZUH=remote`)

VEYRA's demo runs its own Wazuh, but the point of the product is that an organisation **already has
one**. `WAZUH=remote` is that case: VEYRA normalizes locally and ships to a manager it does not own.

```bash
make up WAZUH=remote SERVICES="a3 a6" \
  VEYRA_WAZUH_REMOTE_HOST=siem.internal VEYRA_WAZUH_REMOTE_PORT=514
```

## What changes

Only the `wazuh_main` route's sink. Everything upstream — collectors, engine, contracts, tiers,
lineage, the vault — is identical, which is the whole argument for a pre-processor: the SIEM is a
delivery target, not a dependency.

| | local (default) | remote |
|---|---|---|
| `wazuh_main` sink | `ndjson_file` → `/sinks/wazuh/veyra.ndjson`, tailed by our manager | `syslog_tcp` → `VEYRA_WAZUH_REMOTE_HOST:PORT` |
| our Wazuh containers | started | not started |
| back pressure | none — a local file always accepts | breaker opens after `VEYRA_ROUTE_BREAKER_FAILS` failures |

Routes are data (IF-ROUTES), so switching is a routes document, not a code change:

```yaml
routes:
  - id: wazuh_main
    filter: {tenants: ["*"], tiers: [1, 2, 3, 4]}
    format: ocsf_json
    masking: none
    # host/port omitted on purpose: the sink falls back to VEYRA_WAZUH_REMOTE_HOST/PORT,
    # so one document works on every deployment.
    sink: {type: syslog_tcp}
```

Publish it through control-api (`POST /api/control/routes`, C1) or drop it in
`services/router/routes.default.yaml` for a stack without the control plane.

## Framing

Frames are **RFC 5425 octet-counted**: `<byte-length><space><message>`. That is not decoration — a
VEYRA event is a JSON document that can contain newlines (a joined multi-line event keeps them, by
design), and newline-delimited syslog would split one event into several. A receiver that only speaks
RFC 3164 newline framing will mis-parse VEYRA events; use `http_json` or a file drop instead.

## On the remote manager

1. Accept syslog over TCP:

   ```xml
   <remote>
     <connection>syslog</connection>
     <protocol>tcp</protocol>
     <port>514</port>
     <allowed-ips>10.0.0.0/8</allowed-ips>
   </remote>
   ```

2. Install **the same rules file** — `wazuh/rules/veyra_rules.xml` — into
   `/var/ossec/etc/rules/`. The rule ids (100100–100199) and the fields they match are part of
   IF-WAZUH, so they travel with the events.

3. Confirm with the samples: `wazuh/tests/*.json` plus `tools/wazuh_logtest.py` (point `MANAGER` at
   the remote container, or run the same `wazuh-logtest` command there by hand).

## What A6 verified, and what it did not

**Verified:** the framing, the reconnect-with-backoff, and the breaker transitions
(closed → open after N failures → half-open probe → closed), against a local TCP listener and a
refused port. See `services/router/tests/test_sinks.py`.

**Not verified:** delivery into a second, real Wazuh manager. The demo laptop already runs twelve
containers, and a second manager is another ~700 MB for a path the demo never takes. The gap is the
receiving side's own configuration — the rules and the `<remote>` block above — not VEYRA's sending
side, which is exercised by the tests. Anyone deploying this should run step 3 on their own manager
before trusting it.

## When the remote SIEM goes down

The breaker opens, the router keeps retrying with backoff, and — this is the part that matters —
**offsets do not advance**, because an offset is committed only after every route has flushed
(A6). Nothing is lost; the events are still on `norm.*` with the topic's retention (3 days on the
laptop profile) and are delivered when the far end returns. `veyra_route_breaker_state{route}` is 1
while it is open, and `veyra_route_lag_seconds{route}` climbs, which is what an operator should
alert on.

If the outage is likely to outlast the retention, add a second sink to the same route document (a
local `ndjson_file`) so there is a copy on disk to replay from. That is a routes change, not a code
change.
