# Vendored OCSF subset (IF-OCSF-SUBSET)

`subset_1.9.0.json` is a **hand-written JSON Schema for the fields VEYRA actually maps**, not a
copy of the full OCSF schema. Every class uid, activity id and enum in it was verified against
`https://schema.ocsf.io/api/1.9.0/classes/<name>` on 2026-09-27 (A3), and the file records which
ones.

Two deliberate differences from upstream OCSF 1.9.0, both listed in the A3 report:

1. **`cloud` and `osint` are not required here.** OCSF marks them required on several classes;
   VEYRA is a log pre-processor and does not invent cloud metadata, so demanding them would fail
   every event we produce. Wazuh and the console do not need them.
2. **Only mapped fields are constrained.** `additionalProperties` stays open, because a contract
   may map any field from the catalogue and tier 3 adds `observables`/`unmapped` freely. The
   schema's job is to catch a *wrong* value (a string where an enum int belongs, a malformed IP),
   not to enumerate OCSF.

Validation is per class uid, with compiled validators cached — see `../validate.py`.
