# C6 — Console pages: Onboarding wizard, Contracts, Drift & draft review, Delivery, Audit

```
track: C   owner: C   status: todo
contracts: v1.0
depends_on: [C2, C3, C4, C5]     unblocks: [CP3, Beats 2 and 4]
consumes: [IF-API-CONTROL, IF-API-EVIDENCE (backtest examples, template events), IF-LLM-DRAFT, IF-ULPF]
provides: [/onboard, /contracts, /contracts/:id, /drift, /drift/:id (draft review), /delivery, /audit]
directories: [console/src/pages/{onboard,contracts,drift,delivery,audit}/]
```

## Goal

The pages that carry plug-and-play onboarding (point e) and governed change: onboarding a source from pasted samples in under 30 seconds on stage, reviewing an AI draft against the real bytes, and approving, promoting and replaying with visible safety checks.

## Onboarding wizard (`/onboard`), Beat 2

A single page with a progressive flow, not a multi-page wizard: each section unlocks as the previous one completes, so the presenter never navigates away.

1. **Source.** Name; tenant (fixed for tenant users); transport (HTTP push | syslog UDP | syslog TCP); zone; expected EPS. For syslog: listener + host fingerprint (prefilled from the sample's syslog host).
2. **Samples.** A textarea (paste 1–20 lines; multi-line events separated by a blank line). A **"Paste samples"** button in demo mode fills the scenario's samples (from `GET /api/demo/scenario`). An "Analyze" button.
3. **Analysis** (streams in via SSE):
   - detected layers (`syslog 3164 → json (msg) → text`);
   - template groups with count and drain template;
   - library match, if any: a "Use library pack acme_ngfw_cef" option.
4. **Draft.** Per template: the `DraftReview` component (shared with Drift, below) in compact mode, with the class/activity line, the mapping table with provenance ticks, and the source badge (`llm:model` / `cache` / `heuristic`).
5. **Create contract.** Submit → golden/backtest on the samples → "Waiting for approval". In demo mode, a hint next to the user switcher: "Switch to approver@veyra to approve". The approver sees an **Approve and activate** button (a four-eyes error message if it's the same user).
6. **Key.**
   - For HTTP push: the key card shows `key_id`, the secret (shown once, with a copy button and "I've saved it" confirmation), the HEC endpoint URL and a curl example (from C1).
   - For syslog: the target host/port and listener.
   - A "Download API guide" link to a rendered markdown of A2's `API.md` with the values filled in.

## Contracts (`/contracts`, `/contracts/:id`)
- **List:** contract, tenant, sources, active version, canary version, state chips, last change, author.
- **Detail:**
  - version timeline (draft → testing → canary → active → retired) with actors and times;
  - YAML view (syntax highlighted; read-only here; edits happen through drafts);
  - golden test report;
  - backtest result;
  - a **Diff** tab (semantic diff first, YAML diff second);
  - actions per role: Approve, Promote, Rollback (with confirm dialogs that state the effect, e.g. "Promote authsrv v2. The normalizer switches within 1 second. Events already processed keep revision 1 until you replay them.").
- **Live shadow panel** (if not cut): candidate vs active tier counts since canary start, and regressions (from `shadow_diffs` via evidence-api or a control proxy).

## Drift inbox and draft review (`/drift`, `/drift/:id`), Beat 4

**Inbox:** cards (the exception to the "no card kit" rule, because each drift item is a discrete work item) with source, drain template (mono), count, first/last seen, and draft status (`drafting…` / `draft ready`). New items arrive via SSE with a subtle highlight.

**`DraftReview`** (full mode), laid out as:
```
┌ Raw sample (RawHighlighter; sample switcher 1/5) ───────────────┐┌ Proposed mapping ───────────────────────┐
│ …"msg":"user=▓a.sharma▓ FAILED login from ▓103.21.4.77▓ via ▓10 ││ Class  Authentication / Logon           │
│ .2.3.4▓ attempts:1"} | trace=                                  ││ user.name       ← a.sharma        ✓     │
│   at com.x.Auth.login(Auth.java:88)                            ││ src_endpoint.ip ← 103.21.4.77     ✓     │
└─────────────────────────────────────────────────────────────────┘│ dst_endpoint.ip ← 10.2.3.4        ✓     │
┌ Pattern ───────────────────────────────────────────────────────┐│ status_id       = 2 Failure (constant)  │
│ user=<user> FAILED login from <src_ip:ip> via <dst_ip:ip>      ││ unmapped        attempts               │
│ attempts:<attempts:int>                                        ││ Drafted by llm:qwen2.5:3b in 3.1 s      │
└─────────────────────────────────────────────────────────────────┘└─────────────────────────────────────────┘
┌ Backtest on 8 real events ─────────────────────────────────────────────────────────────────────────────────┐
│ Tier 3 → tier 1: 8   Regressions: 0   Unchanged: 0      [View events]                                       │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
[Submit for approval]   (then, as approver) [Approve]  [Promote]  [Replay 8 events ▮▮▮▮▮▮▯▯ 6/8]
```

- **Hover** a mapping row → its span highlights in the raw with the thread (C5).
- **Edit** a row: an OCSF field select (catalogue), a token select (the tokens of the sample, shown as chips over the raw text; click a chip to assign), or a constant (enum select). Every edit → `PATCH /drafts/{id}` → provenance + backtest re-run; rows update in place.
- **Row states:**
  - ✓ provenance ok;
  - ✗ with the reason;
  - amber "review" when the LLM and heuristic disagree (C4).
- **Actions** follow the lifecycle. Each button's label changes to the past tense when done ("Approved", "Promoted"). The replay progress comes from SSE `replay` events. When done: "8 events replayed. View in Lineage", deep-linking to the first event (B6 route).
- **"View events"** → Lineage search by `template_sig`.

## Delivery (`/delivery`)
- **Per route:** filter summary, format, masking, sink, delivered/min (sparkline), failed, filtered, lag, breaker state.
- **Recent receipts** table (event, revision, status, time), linking to Lineage.

## Audit (`/audit`)
- A filterable table: actor, role, action, target, time, detail.
- Export CSV.
- Platform/auditor roles see all; tenant users see their tenant only.

## Tasks
- [ ] 1. The `DraftReview` component (compact + full), with edit, hover-thread and row states.
- [ ] 2. The onboarding page with streaming analysis, the demo "Paste samples" button, and the key card.
- [ ] 3. Contracts list + detail + diff + lifecycle actions + confirm dialogs.
- [ ] 4. The drift inbox + draft review flow + replay progress.
- [ ] 5. Delivery and Audit pages (cut-able polish).
- [ ] 6. Hindi strings for the onboarding and drift pages.
- [ ] 7. Playwright flows:
  - onboarding end-to-end with two users (four-eyes);
  - drift → approve → promote → replay → the Lineage link opens rev 2.

## Acceptance criteria
- [ ] AC1: The Beat 2 flow completes in ≤ 25 s of clicking with the cached draft (timed in rehearsal).
- [ ] AC2: The Beat 4 flow completes in ≤ 40 s, including the replay (timed).
- [ ] AC3: The same user can't approve their own submission; the UI shows the four-eyes message.
- [ ] AC4: An edit that breaks provenance shows ✗ immediately and disables Submit, with the reason visible.
- [ ] AC5: All pages work via keyboard; visible focus; no layout shift on SSE updates.

## Implementation notes
_(filled after execution)_
