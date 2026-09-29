# C6 — Console pages: Onboarding wizard, Contracts, Drift & draft review, Delivery, Audit

```
track: C   owner: C   status: in-progress
contracts: v1.5
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

<!-- synced from C2/C3 --> SSE payloads to consume (IF-API-CONTROL v1.4):
- `contract` `{id, version, state, action}`;
- `replay` `{job_id, contract_id, status, total, published, normalized, detail}`;
- `drift` `{drift_id, source_id, template_sig, count, state, created}`.

`GET /replay?contract_id=` lists jobs. Four-eyes refusals are 403 with a message containing `four-eyes`.

<!-- synced from C5 --> C5 is built (`console/`, report [C5.md](../reports/C5.md)). Build each page under
`src/pages/<name>/` and swap its `PlaceholderPage` route in `shell/AppShell.tsx` (nav entries with
`phase: "C6"` in `shell/nav.ts`). Reuse `DataTable`, `Drawer`, `ConfirmDialog`, `useToast`, `api`/`queryKeys`,
and test with `renderWithProviders` (`src/test/render.tsx`) plus `signInAs`/`resetMockState`
(`src/mocks/handlers.ts`), adding MSW handlers for every endpoint you call. Every string goes into both
`en.json` and `hi.json` (a parity test enforces it). `useLiveUpdates` already toasts `contract`, `drift`
and finished `replay` events; add `drift`/`replay` query invalidation there. The Sources drawer's "recent DLQ
template sigs with counts" is still a link to `/drift?source=<id>`; fill it from `GET /drift`.

## Tasks
- [x] 1. The `DraftReview` component (compact + full), with edit, hover-thread and row states.
  <!-- synced from S0 --> Same toolchain as C5: Node 25.2.1 (IF-VERSIONS, D17). No separate setup.
- [x] 2. The onboarding page with streaming analysis, the demo "Paste samples" button, and the key card.
- [x] 3. Contracts list + detail + diff + lifecycle actions + confirm dialogs.
- [x] 4. The drift inbox + draft review flow + replay progress.
- [x] 5. Delivery and Audit pages (cut-able polish).
- [x] 6. Hindi strings for the onboarding and drift pages.
- [x] 7. Playwright flows (mock mode; the Lineage link's target is B6's):
  - onboarding end-to-end with two users (four-eyes);
  - drift → approve → promote → replay → the Lineage link opens rev 2.

## Acceptance criteria
- [ ] AC1: The Beat 2 flow completes in ≤ 25 s of clicking with the cached draft (timed in rehearsal).
- [ ] AC2: The Beat 4 flow completes in ≤ 40 s, including the replay (timed).
- [x] AC3: The same user can't approve their own submission; the UI shows the four-eyes message.
- [x] AC4: An edit that breaks provenance shows ✗ immediately and disables Submit, with the reason visible.
- [ ] AC5: All pages work via keyboard; visible focus; no layout shift on SSE updates.

## Implementation notes
<!-- synced from C6 --> Built on branch `c6-console-pages` (2026-09-29). Full detail in [reports/C6.md](../reports/C6.md).

- **Backend (TC41):** `GET /routes`, `POST /onboarding/use-library`, and re-drafting a `drafting` item (IF-API-CONTROL v1.5). A superseded draft that finishes late no longer changes its drift item.
- **Four-eyes on stage:** control-api answers a role 403 before the four-eyes 403, so the author clicking Approve sees "this action needs one of the roles: admin, pack_approver"; an approver approving their own submission sees the four-eyes text. The UI shows either verbatim beside the button (AC3).
- **Drift inbox:** the "Open" chip is every unresolved item (open, drafting, draft ready), grouped on the page, because `GET /drift?state=` is an exact match. `?source=` (from the Sources drawer) filters by source.
- **Demo fallback (TC37):** the drift page polls a drafting draft and, in demo mode, re-requests it with `mode: "cache"` after 5 s.
- **Mock mode:** `make console-mock` runs every page against a post-Beat-2 world (authsrv@1 active, the T3 drift item draft-ready); onboarding creates `src_<name>_01`. `make console-e2e` runs Beats 2 and 4 in it (serially, one worker).
- **Tests:** 191 console unit tests, 3 Playwright flows, 156 control-api tests.
- **Refinements** (after a walkthrough): Dismiss on the drift page, refused edits and drafts shown, no field mapped twice; see the report.
