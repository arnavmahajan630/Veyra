# Inventory reload — verified behaviour (IF-INVENTORY)

A1 had to answer one question: when control-api (C1) rewrites
`edge/vector/inventory/sources.csv`, how soon does the edge resolve sources by the new row,
and what has to happen to make it notice?

## What was measured

Vector `0.58.0-debian`, started with `--watch-config`, enrichment table declared as
`[enrichment_tables.sources]` of `type = "file"` with a CSV encoding.

| Change | Row honoured? | Within |
|---|---|---|
| Append a row to `sources.csv`, touch nothing else | **yes** | < 5 s |
| Append a row **and** touch `edge/vector/reload.stamp` | yes | < 5 s |

So on the pinned version **Vector watches the enrichment-table file itself**. The
`reload.stamp` file the plan assumed would be needed is not: touching it changes nothing,
because it is not part of the config Vector loads.

Evidence: `tests/int/test_edge.py::test_ac4_inventory_row_is_picked_up` (AC4) adds a row,
waits 5 s, sends an event from the new host and asserts it resolves to the new `source_id`.

## What C1 should do

1. Write `sources.csv.tmp`, then `rename()` it over `sources.csv`. The atomic rename still
   matters — it is what stops Vector reading a half-written file.
2. Touching `reload.stamp` is optional and currently a no-op. It is kept in the repo as a
   harmless hook in case a future Vector version stops watching enrichment tables.
3. No container restart is needed. The A1 fallback (control-api restarting the edge over the
   Docker API) is therefore **not** required on this version.

## Caveat worth knowing

A config reload tears the topology down and builds it again. An event that arrives inside
that window can be lost — observed once during development, and the reason
`tests/int/test_edge.py` retries a send before failing. Two consequences:

- C1 should batch inventory changes rather than rewriting the file per source.
- A reload is not free during the demo; the onboarding in Beat 2 issues an API key and uses
  the HTTP gateway (A2), which needs no inventory change at all, so the demo path avoids it.
