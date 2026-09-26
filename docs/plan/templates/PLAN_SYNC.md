# Plan sync procedure (run after every phase)

Give this to the agent after the report is written, or include it in the kickoff prompt.

```
Perform the VEYRA plan sync for phase <PHASE_ID>:

1. Open reports/<PHASE_ID>.md. For each row in "Interfaces touched" marked additive or breaking:
   a. Edit 02_CONTRACTS.md: update the section, bump the version per §0
      (additive → minor, breaking → major).
   b. Run: grep -rln "<IF-ID>" docs/plan/ . For every file listed (including other tracks' phase files):
      - update the text that references the changed interface;
      - update its header "contracts: vX.Y";
      - add "<!-- synced from <PHASE_ID> -->" next to each edit.
   c. Add an entry to the top of 05_CHANGELOG.md using its format, with ACTION REQUIRED lines
      for every affected owner (@A/@B/@C) and the list of files you patched.
2. Fill in the "Implementation notes" section at the bottom of the phase file itself.
3. For each remaining phase file in THIS track: apply "Notes for downstream phases" from the report
   (paths, approaches, removed or added tasks, new risks). Keep the edits surgical.
4. If a note affects ANOTHER track's future phase (not an interface): do NOT edit their file; add a
   changelog entry "REQUEST @X" or "CLARIFICATION" with the note.
5. Update 06_STATUS_BOARD.md: status, one-line summary, report link, contracts version.
6. If the phase changed a knob or default: update 03_INFRA_PROFILES.md and profiles/*.env.
7. If the phase changed anything the demo shows: update 04_DEMO_SCRIPT.md beats and fallbacks.
8. Run `make plan-check` and confirm there are no stale headers in files you touched.
9. Commit all plan changes in ONE commit: "plan-sync: <PHASE_ID>".
```
