# C5 — Console shell, design system, RawHighlighter, Overview, Sources

```
track: C   owner: C   status: in-progress
contracts: v1.4
depends_on: [S0, B1 (query fixtures), B4 (OpenAPI), C1 (OpenAPI)]   unblocks: [CP2, B6, B7 (panel), C6]
consumes: [IF-API-CONTROL, IF-API-EVIDENCE, IF-API-DEMO, IF-ULPF]
provides: [console shell, design system, RawHighlighter + thread overlay, hotkey registry, i18n, /overview, /sources]
directories: [console/ (except pages/lineage, pages/evidence, pages/demo)]
```

## Goal

The frame every page lives in, and the two pages that show unified visibility (problem statement point f): a live Overview of the whole pipeline, and per-source health. Plus the shared pieces the other pages need:
- the design system;
- `RawHighlighter` with the "thread";
- SSE hooks;
- the hotkey registry;
- i18n;
- typed API clients.

**Start on day one against fixtures.** The console is what the judges look at most.

## Stack

- Vite + React + TypeScript (strict) + Tailwind, with a small in-house component set. No component-kit look: shadcn primitives are allowed only as unstyled behaviour (dialogs, popovers), restyled to our tokens.
- **Routing:** React Router.
- **Server state:** TanStack Query.
- **Types:** `openapi-typescript` from the control-api and evidence-api OpenAPI (`make console-types`, run in CI; a type diff fails the build).
- **Fonts self-hosted** in `console/public/fonts/` (the demo is **air-gapped**; no Google Fonts CDN at runtime). Licenses are checked (OFL).
- The build outputs a static bundle served by Caddy. During development, the Vite dev server sits behind Caddy on the same origin.

## Design system

Grounded in the subject. *Veyra* means thread. The product's job is to keep an unbroken thread from any normalized field back to its original bytes and on to a signed root. The audience is SOC operators and government evaluators in India, on projectors and 1080p laptop screens.

**Colour** (tokens in `console/src/design/tokens.css`, as CSS variables):

| Token | Hex | Use |
|---|---|---|
| `paper` | `#FBFBF8` | Page background (cool off-white, not cream) |
| `ink` | `#1D2330` | Text, primary lines |
| `rule` | `#D9DDE3` | Hairlines, table rules |
| `thread` | `#2E3A8C` | Indigo: primary actions, the lineage thread, links |
| `turmeric` | `#C98A0B` | Byte highlights only (marker-pen feel on raw text) |
| `tier1` | `#2F7D5B` | match |
| `tier2` | `#B7791F` | partial |
| `tier3` | `#6B5CA5` | unknown template: violet, because unknown is not an error |
| `tier4` | `#A23B3B` | unparseable, verification failure |

A dark theme is optional and may be cut. Contrast: every text/background pair meets WCAG AA. Check with a script (`make console-contrast`).

**Type:**
- **Mukta** for all UI text. It covers Latin and Devanagari in one family, so the EN/HI toggle doesn't change the visual voice.
- One monospace face (e.g. JetBrains Mono or IBM Plex Mono, self-hosted) used **only** for raw log bytes, hashes and ids, where fixed width carries meaning. Never for labels.
- Scale: 13 / 15 / 18 / 24 / 32 px; line-height 1.5 for body and 1.35 for mono; weights 400/500/600. Sentence case everywhere. No all-caps labels, no eyebrow labels above headings.

**Layout:**
- A left rail nav (icons + labels), 64 px collapsed / 208 px expanded; a content area with a max width of 1600 px; left-aligned.
- Tables are the main surface: dense, with hairline rules and no card shadows.
- Radius: 6 px for inputs and buttons; 0 px for tables and panes. Hierarchy comes from rules and whitespace, not from stacks of cards.

**Signature element:** the thread. Anywhere a normalized value is shown next to its raw source, hovering draws an indigo SVG thread from the value to the turmeric-highlighted bytes. It is the one bold, animated thing in the product (short path draw, 180 ms). Everything else stays quiet: no entrance animations, no gradient washes, no decorative numbering.

**Writing:** plain verbs, sentence case, active voice. Buttons say what happens: "Approve and activate", "Replay 8 events", "Verify evidence". Errors say what happened and what to do. Empty states invite action: "No sources yet. Onboard your first source."

**Review before building.** The implementing agent writes a 10-line design note in the report: what could read as generic and what was changed. Check against the frontend-design guidance's list of defaults: cream + terracotta, dark + acid accent, card-kit, eyebrow labels, middle-dot meta strings, arrow-suffixed buttons.

## Shared components (stable APIs; B6 depends on them)

| Component | API |
|---|---|
| `RawHighlighter` | `{text: string, spans: {id, startChar, endChar, tone: "active"\|"pinned"\|"muted"}[], activeId?, onSpanHover?}`. Renders mono text preserving whitespace and newlines; highlights spans; exposes span DOM rects via a ref for the thread overlay. Virtualizes above 500 lines. |
| `ThreadOverlay` | `{from: DOMRect\|null, to: DOMRect\|null}`. An absolutely positioned SVG path (cubic curve) between the two rects; `prefers-reduced-motion` → no draw animation. |
| `byteSpanToChar(text, [start,end])` | Utility. B6 owns its tests; C5 exports it. |
| `TierChip`, `TierBar` (stacked, over time), `StatusDot`, `Kbd`, `DataTable` (sortable, sticky header, keyboard row focus), `Drawer`, `ConfirmDialog`, `Toast` | Standard building blocks |
| `useSSE(url, handlers)` | Reconnects with backoff; the connection state is shown in the header as a small dot |
| `useHotkeys` registry | `registerHotkey({combo, description, handler, when?})`. A global listener ignores keystrokes while typing in inputs. `?` shows the hotkey sheet. |
| `t(key)` i18n | Plain JSON dictionaries `en.json` and `hi.json` (the Hindi strings are reviewed by a team member who reads Hindi). A header toggle; persisted in `localStorage`, which is fine in our own served app. |

## Shell
- **Login page;** a session check; role- and tenant-aware nav.
- **Header:** tenant switcher (platform users), user menu, SSE status dot, language toggle.
- **Demo mode** (`/api/control/auth/me` returns `demo_mode`): a **user switcher** in the header that logs in as the other seeded user in one click (for the four-eyes beat), and the `/demo` route enabled (B7).
- **Nav:**
  - Overview
  - Sources
  - Onboard source
  - Contracts
  - Drift
  - Lineage (B6)
  - Evidence (B6)
  - Delivery
  - Audit
- **Toasts** for SSE events: a new drift item, a contract promoted, replay done.

## Overview page (`/`)

The first thing seen in Beat 1: the pipeline as a living thread, left to right.

```
Sources ─── Edge / Gateway ─── Kafka ─┬─ Normalizer ─── Router ─── Wazuh
 (per zone)                           ├─ Vault (segments, last seal)
                                      └─ Lineage index
```

- **Stages as nodes** with live numbers (EPS in, EPS out). The connecting lines' stroke width scales with EPS. The line stays still; the numbers update: no marching-ants animation.
- **Tier bar:** stacked tier 1–4 over the last 15 minutes (1 s updates via SSE), with the current mix as percentages.
- **Sources strip:** one row per source with name, zone, tier mix mini-bar, EPS and last seen. Clicking opens the Sources drawer.
- **Evidence status:** the last sealed segment age, the last signed root with its window and immudb badge, and the chain intact (✓ / ✗ linking to Evidence).
- **Delivery status:** per route, delivered/min, lag and breaker state.
- **Data source:** `GET /api/lineage/overview` + `/lineage/stream` SSE, plus control-side SSE for sources and contracts.

## Sources page (`/sources`)

**Source Health table** (v1 §12): source, tenant, zone, transport, contract ref, expected EPS vs actual (with a delta indicator), last seen, tier mix, clock skew p50, and status.

**Row drawer:**
- source details;
- keys (issue/revoke, per role);
- the contract version history link;
- recent DLQ template sigs with counts (links to Drift);
- a link to lineage search filtered by source.

**Silent-source alert:** actual EPS 0 for more than 60 s while expected > 0 → an amber row with "No events for 1m 20s". This is v1's missing-logs detection, cheap and visible.

<!-- synced from C2 --> `POST /auth/demo-switch {email}` (demo mode) and `GET /sources/{id}/keys` now exist in control-api (IF-API-CONTROL v1.4).

## Tasks
- [x] 1. Vite + TS + Tailwind scaffold; tokens; self-hosted fonts; contrast check script.
  <!-- synced from S0 --> Toolchain is **Node 25.2.1 / npm 11.7.0** (IF-VERSIONS, decision D17),
  which is what the demo laptop and the CI `console` job both use. Put `"engines": {"node": ">=25"}`
  in `console/package.json`, commit `package-lock.json` (CI runs `npm ci`), and build to
  `console/dist/` — Caddy already serves that path read-only (see `Caddyfile`, created in S0).
  If a Vite or Tailwind major refuses Node 25, switch the build to a `node:22` container
  per D17's revisit note instead of changing the host toolchain.
- [x] 2. Typed API clients from OpenAPI (fixtures mode via MSW for offline UI work).
  <!-- synced from C5 --> The clients and the MSW fixtures mode are done; the types are hand-written
  (TC14) until control-api and B4 publish OpenAPI. `make console-types` is still to do.
- [x] 3. The shell: login, nav, header, demo user switcher, toasts, SSE hook, hotkey registry, i18n.
- [x] 4. The shared components listed above, with a `/dev/components` route showing each in states (development only).
- [x] 5. The Overview page.
- [x] 6. The Sources page + drawer + silent-source alert.
- [x] 7. Playwright smoke: login → Overview live numbers change within 3 s → Sources shows the NTRO sources.
- [x] 8. The design note in the report (the default-avoidance review).

## Acceptance criteria
- [ ] AC1: Overview numbers and the tier bar update live within 2 s of traffic changes (human observes during CP2).
- [ ] AC2: First load < 1 s on the laptop from the Caddy static build; no network requests leave localhost (checked in the browser devtools network tab, offline).
- [x] AC3: `RawHighlighter` + `ThreadOverlay` work on the dev route with ASCII, JSON-escaped and Devanagari samples.
- [x] AC4: The EN/HI toggle switches the shell and Overview labels.
- [x] AC5: The demo user switcher swaps `author@maha` ⇄ `approver@veyra` in one click.

## Implementation notes
<!-- synced from C5 --> Built on branch `c5-console-shell` (2026-09-29). Full detail in [reports/C5.md](../reports/C5.md).

- **Stack as pinned:** Vite 7.3.6, React 19.3.0, react-router 7.18.4, TanStack Query 5.104.0, Tailwind 4.3.3, vitest 4.1.11, jsdom 27.4.0, TypeScript 5.9.3, MSW 2.15.0, Playwright 1.63.0, lucide-react 1.48.0. These are the newest majors whose `engines` accept Node 25 (TC12). npm 10.8 crashes on jsdom's optional `canvas` peer; use npm 11.
- **Mock mode is the default workbench:** `make console-mock` runs every page against MSW fixtures, and the 126 unit tests use the same handlers in Node. The production build compiles the mock world out.
- **Dev against the stack:** `make console-dev` proxies `/api` from Vite (:5173) to Caddy (:8080), so the browser sees one origin (TC13, D16).
- **Fonts:** `@fontsource/mukta` and `@fontsource/jetbrains-mono`, bundled by Vite (no `public/fonts/`).
- **AC1 and AC2** pass against the mock (numbers move within 3 s; no outside URLs in the bundle). Their live halves wait for B1/B4's `/lineage/overview`, `/lineage/sources` and `/lineage/stream` (CP2).
- **The drawer's "recent DLQ template sigs with counts"** is a link to Drift filtered by source; C6 can fill it from `GET /drift`.
