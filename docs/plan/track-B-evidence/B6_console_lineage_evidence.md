# B6 — Console pages: Lineage Explorer + Evidence

```
track: B   owner: B   status: done
contracts: v1.5
depends_on: [B4, C5 (shell, design system, RawHighlighter)]   unblocks: [CP3, Beat 5]
consumes: [IF-API-EVIDENCE, IF-ULPF (field_offsets, derived_fields), C5 design tokens/components]
provides: [console routes /lineage, /lineage/:uid, /evidence]
directories: [console/src/pages/lineage/, console/src/pages/evidence/]
```

## Goal

The two pages that carry Beat 5 and point (d) of the problem statement:
- **Lineage Explorer:** find any event, see its raw bytes and its normalized form side by side, hover a field to see exactly which bytes it came from, follow its revisions and deliveries, and verify it cryptographically with an animated step-by-step panel.
- **Evidence:** the signed-root ledger, public key and export.

This is the signature moment of the console: the "thread" between a normalized field and its raw bytes (C5 design principle).

## Early start

Don't wait for C5 to finish. On day one of B6:
1. Build components in `console/src/pages/lineage/components/` against B4's OpenAPI fixtures, using plain Tailwind.
2. When C5 lands its tokens and `RawHighlighter`, swap to them. The component APIs below are agreed with C5 up front (log any change as a changelog REQUEST).

## Design

### Lineage Explorer: `/lineage`
- **Search bar:** accepts `event_uid`, sha prefix, IP, user or `template_sig`. It calls `GET /api/lineage/search`. Results list: time, source, tier chip, class, `raw_preview` (mono, one line, ellipsis), revision count.
- **Deep links:** `/lineage/:uid`. Other pages (Drift draft view "View events", Sources drawer) link here.

### Event detail: `/lineage/:uid`
Layout (desktop, 1920 px):
```
┌───────────────────────────────────────────────────────────────────────────────┐
│ Event 0192a4f0…   Auth Server (src_authsrv_01)   Maha Power   dmz   HTTP push  │
│ [rev 1  tier 3  unknown_template] ──► [rev 2  tier 1  authsrv@2  replay job …] │ revision timeline
├─────────────────────────────────────┬─────────────────────────────────────────┤
│ RAW (exact bytes, mono)             │ NORMALIZED (OCSF, rev selector)          │
│ <134>Sep 26 14:05:11 fw01 app[233]: │ class  Authentication / Logon / Failure  │
│ {"evt":"auth","msg":"user=▓a.sharma▓│ user.name        a.sharma          ✓     │
│ FAILED login from ▓103.21.4.77▓ …   │ src_endpoint.ip  103.21.4.77       ✓     │
│   at com.x.Auth.login(Auth.java:88) │ status_id        2 Failure   (constant)  │
│ sha256 42d8…ab57  177 bytes  utf-8  │ unmapped ▸  enrichments ▸  ulpf ▸        │
├─────────────────────────────────────┴─────────────────────────────────────────┤
│ VERIFY  [Verify evidence]   ● fetch ● decrypt ● hash ● chain ● digest ● merkle ● sig ● immudb │
│ Deliveries: wazuh_main rev 1 ✓ rev 2 ✓     partner_masked rev 2 ✓                                  │
│ Vault: seg_raw.custom_1_…  record 14  sealed 14:05:31  window w_1790000060                     │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Raw pane:**
- Uses C5's `RawHighlighter`.
- Offsets arrive as **byte** spans (IF-ULPF). Convert them to UTF-16 string indices in the UI: `TextEncoder` over the decoded text, then build a byte→char index once per event. Test with Devanagari (the OT historian event).
- Line breaks are preserved; invisible characters are rendered as `·` markers only for whitespace-only regions.
- Footer: sha256, byte length, encoding.

**Normalized pane:**
- A field list for mapped OCSF paths, ordered by catalogue importance, with collapsible `unmapped`, `enrichments`, `observables` and raw `ulpf` JSON.
- Each row shows:
  - ✓ if the provenance check passes (computed by the API or on the client using the same algorithm);
  - "constant", "vocabulary", "timestamp" or "derived" badges for `derived_fields`.

**Hover interaction (the signature moment):**
- Hovering a normalized row highlights its byte span in the raw pane (turmeric marker) and draws an SVG **thread** from the row to the highlighted span.
- Clicking pins the highlight.
- Keyboard: arrow keys move through fields, Enter pins.
- The thread is the only animated decoration on the page, and it respects `prefers-reduced-motion` (no animation; the highlight still shows).

**Revision timeline:**
- A chip per revision; selecting one switches the normalized pane.
- The rev 2 chip shows contract ref and replay job id.
- A diff toggle shows changed fields between revisions (green/violet).

### Verify panel
- The button calls `GET /api/evidence/verify/{uid}`.
- The 8 steps render as a horizontal chain of nodes connected by a thread line. They reveal in order with ~120 ms stagger **after** the response arrives: this is a presentation of the real result, not a fake progress bar. Honesty rule: never animate green before the server says so.
- Each node shows its label (IF-API-EVIDENCE labels) and ms. Clicking a node shows `detail`.
- **Failure:**
  - failed nodes turn red and the thread breaks visually at the first failure;
  - a summary sentence built from the failing steps' `detail` appears below (e.g. "Segment altered after sealing. The signed root is intact, so this change happened after 14:05:31");
  - "pending seal" steps render grey with "sealing in ≤ N s" and auto-retry once when due.
- The `Shift+T` hotkey (registered via C5's hotkey registry) calls `POST /api/demo/tamper {mode: insider_rewrite, event_uid: <current>}`, then re-runs verify automatically after 600 ms. Demo mode only.

### Evidence page: `/evidence`
- **Roots ledger table:** window, time range, leaf count, root (short), signature ✓, immudb ✓ (badge fetched lazily per row), prev link ✓. Live-appending via SSE.
- **Chain strip:** the last 30 windows as linked beads. A broken link turns red (driven by `ledger_audit` results exposed by `/evidence/roots`).
- **Export panel:** pick an event (search) → "Download evidence package" (zip). Shows what's inside and the one-line offline verify command.
- **Public key:** fingerprint (SHA-256 of the DER) + copy + download.
- **Tamper matrix explainer:** a static card linking `docs/tamper_matrix.md` content (modes × steps), rendered as a compact table. It's useful in Q&A.

### i18n
All static labels go through C5's `t()`. Provide `en` and `hi` strings for this page's labels. Step labels come from the API in English; map step ids to i18n keys client-side.

### Performance
- The event detail renders in < 300 ms after data arrives.
- Raw panes up to 64 KB render without jank (virtualize lines above 500 lines).

## Tasks
- [x] 1. Components against fixtures: `SearchBar`, `ResultsList`, `RevisionTimeline`, `NormalizedFieldList`, `VerifyChain`, `DeliveriesList`, `VaultLocation`.
- [x] 2. The byte→char offset utility + unit tests (ASCII, multi-byte, CRLF, emoji).
- [x] 3. Integrate with C5's `RawHighlighter` and thread overlay; hover, pin and keyboard behaviour.
- [x] 4. The verify panel with honest reveal, failure summary and pending-seal state.
- [x] 5. The Evidence page: ledger table (SSE), chain strip, export, public key.
- [x] 6. The `Shift+T` hotkey hook (demo mode) + auto re-verify.
- [x] 7. Playwright smoke test: open a T3 event → hover `src_endpoint.ip` on rev 2 → the highlighted text equals `103.21.4.77`; verify → 8 green.

## Acceptance criteria
- [x] AC1: Hovering any mapped field highlights exactly the bytes of its value (Playwright check on 3 fields, including a JSON-escaped value and a Devanagari value).
- [~] AC2: Verify shows **7 green + 1 grey** steps (immudb is a prototype; see the
      CLARIFICATION of 2026-09-30). Behaviour proven in mock mode; the < 2 s cold timing is
      deferred to the rehearsal.
- [x] AC3: After `Shift+T`, verify turns red at the expected steps with the locating sentence; after untamper (demo panel), it's green again.
- [x] AC4: The Evidence page shows new roots appearing live every window.
- [x] AC5: Keyboard-only use works: search → open → move through fields → verify. Focus is visible.

## Implementation notes

Done 2026-09-30. Full write-up in [reports/B6.md](../reports/B6.md).

The work was less "build the pages" than "make them true". The components existed; what they
showed was partly invented:

- **Byte offsets never reached the client.** `ulpf.field_offsets` is produced by the engine and
  indexed by `veyra_lineage.rows`, but `Revision` did not expose it, so the pane highlighted
  spans from hardcoded literals and ticked every field as verified. Offsets are now lifted onto
  `Revision`, and each located field is checked against the raw bytes with the engine's own rule
  (`pages/lineage/fields.ts`, mirroring `provenance_check`). A field whose span does not slice
  out its value now shows a red provenance warning.
- **The vault-scan fallback fabricated a revision** with an invented OCSF body. It now returns
  no revisions and `index_available: false`, and the page says so.
- **`npm run build` was broken** (26 `tsc` errors), including a `useSSE` misuse that meant the
  ledger table had never live-appended. 0 errors now.
- **`t(key) || "fallback"` is dead code** — `translate()` returns the key, which is truthy. 94
  keys added to `en`/`hi` (404 each, in sync).

Additive API changes: `Revision.field_offsets`/`derived_fields`, `RawInfo.raw_text`/`raw_b64`,
`event: root` on `/lineage/stream`, ledger-audit flags on `/evidence/roots`, and a
`pending_seal` verify status so an unsealed window reads as early rather than broken.

Verified by 247 console unit tests, 6 Playwright flows (`e2e/beat5.spec.ts` covers AC1/AC3/AC5)
and 33 evidence-api tests. AC2's live timing is the one open item.
