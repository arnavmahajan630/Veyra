# S2 — Demo hardening, rehearsal, backup, pitch alignment

```
track: shared   owner: presenter + all   status: todo
contracts: v1.0
depends_on: [CP3]   unblocks: [demo day]
```

## Goal

Turn a working system into a demo that cannot fail visibly, and align the PPT with what's actually built.

## Tasks

### Reliability
- [ ] 1. `make demo-preflight` (B7 implements; everyone contributes checks):
  - all healthy;
  - memory headroom ≥ 3 GB;
  - Ollama model loaded on GPU;
  - LLM cache contains the T3 draft;
  - Wazuh rule ids 100100–100131 loaded (`/var/ossec/bin/wazuh-logtest` sample);
  - clock sync;
  - disk free ≥ 10 GB;
  - no stale segments open;
  - baseline traffic flowing.
- [ ] 2. **Failure injection.** Run the demo with each of these, one at a time, and confirm the fallbacks in `04_DEMO_SCRIPT.md` §6 work:
  - normalizer killed mid-beat-3 (auto-restart by compose `restart: unless-stopped`; events catch up);
  - Ollama stopped (cache fallback);
  - Wazuh dashboard slow (keep narrating);
  - browser reload on every page (state is server-side);
  - hotkey pressed twice (stages are idempotent).
- [ ] 3. **Warm starts.** Measure the first page load after reset and the first verify after reset. Pre-warm in `demo-reset` if either is over 1 s.
- [ ] 4. **Rehearsal loop.** 10 full live rehearsals with a timer. Log each beat's duration in `reports/S2-rehearsals.md`. Trim narration until every run is ≤ 3:00 with 10 s spare.

### Backup
- [ ] 5. Record a clean 1080p run with OBS (screen + narration), following the exact script. Save it on the desktop and a USB stick. Also export a silent version for playing under live narration.
- [ ] 6. Screenshots of every beat, for PPT slides and as a last-resort fallback.

### Pitch alignment
- [ ] 7. Architecture slide: v1's 8 layers with a clear "running in demo" highlight on the built components; "slide-only" components in a muted style. Must match `00_MASTER.md` §5.1 exactly.
- [ ] 8. Numbers slide: only measured numbers, taken from:
  - `reports/A6-bench-*.md` (EPS per normalizer replica, scaling curve);
  - `reports/C4-bench-*.md` (draft accuracy, latency);
  - B4 (verify latency).

  Label v1's targets (60k EPS, 1B/day) as design targets.
- [ ] 9. Q&A drill using `04_DEMO_SCRIPT.md` §7. Each person answers 5 random questions in under 30 s.
- [ ] 10. Deviations sheet: `00_MASTER.md` §7 on one slide in the appendix.

### Freeze
- [ ] 11. Tag `demo-final`. Build the console static bundle. `docker save` all images to disk, so no pulls are needed at the venue.
- [ ] 12. Machine prep checklist (from `04_DEMO_SCRIPT.md` §5) printed on paper.

## Acceptance criteria
- [ ] AC1: 10/10 rehearsals at ≤ 3:00 with no manual recovery.
- [ ] AC2: Every failure injection recovers as documented.
- [ ] AC3: The backup video exists in two places.
- [ ] AC4: Every number on the slides has a source report.

## Implementation notes
_(filled after execution)_
