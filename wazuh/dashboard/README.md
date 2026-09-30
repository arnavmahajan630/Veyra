# Dashboard saved objects

`saved_objects.ndjson` holds the two Discover searches the demo opens, plus the `wazuh-alerts-*`
index-pattern they reference (exported with `includeReferencesDeep`, so an import into a fresh
dashboard cannot land half-configured):

| Saved search | Query | Why |
|---|---|---|
| **VEYRA events** | `rule.groups: veyra` | Everything VEYRA delivered, any tier. This is the "our logs are in your SIEM" view — Beat 3 opens it. |
| **VEYRA alerts level 5+** | `rule.groups: veyra and rule.level >= 5` | Only what an analyst would act on: auth failures and brute force. Tier 3/4 noise is excluded by the level, not by a hand-maintained list. |

## Import

```bash
curl -sk -u admin:admin -H "osd-xsrf: true" \
  -X POST "https://localhost:8443/api/saved_objects/_import?overwrite=true" \
  --form file=@wazuh/dashboard/saved_objects.ndjson
```

`overwrite=true` on purpose: a demo reset runs repeatedly on the same machine and must be
idempotent.

## Export (after changing a search in the UI)

```bash
curl -sk -u admin:admin -H "osd-xsrf: true" -H "Content-Type: application/json" \
  -X POST "https://localhost:8443/api/saved_objects/_export" \
  -d '{"objects":[{"type":"search","id":"veyra-events"},
                  {"type":"search","id":"veyra-alerts-level5"}],
       "includeReferencesDeep":true}' \
  -o wazuh/dashboard/saved_objects.ndjson
```

The ids are fixed (`veyra-events`, `veyra-alerts-level5`) rather than generated, so re-exporting
after an edit produces the same objects instead of a second copy.

**@B (B7):** the reset should import this file after re-creating the indices. The searches are
useless without the alerts they point at, so import *after* the stack is serving, not before.
