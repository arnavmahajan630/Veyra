# Agent kickoff prompt

Copy everything inside the fence into a new agent session. Fill in the `<…>` placeholders. Attach or paste the listed files.

```
You are a senior engineer implementing phase <PHASE_ID> of the VEYRA demo, a Python + React system.
You work in the git worktree at <PATH> on branch <BRANCH>. You own ONLY these directories: <DIRS from phase file>.

Read, in this order:
1. docs/plan/00_MASTER.md                  (whole-project context; principles P1–P8 are non-negotiable)
2. docs/plan/02_CONTRACTS.md sections: <IF-IDs listed in the phase file header>
3. docs/plan/<track>/<X00_TRACK.md>
4. docs/plan/<track>/<PHASE_FILE>
5. docs/plan/reports/<reports of phases this one depends on>
6. docs/plan/05_CHANGELOG.md entries since contracts <version in phase header>

Before writing code:
- Restate the goal in 3 lines, list the tasks you will do in order, and list any gaps or conflicts
  between the phase file and 02_CONTRACTS.md. Stop and wait for my OK.

While coding:
- Interfaces come only from 02_CONTRACTS.md. Additive gaps: implement them, then log them for the
  plan sync. Breaking gaps: stop and ask.
- No magic numbers: every limit, interval or size goes through veyra_common Settings (VEYRA_*),
  with the default in profiles/laptop.env.
- Pydantic models for every record; validate inputs; bad input → DLQ/error with a reason, never a crash.
- Every service: /healthz, /metrics, structured JSON logs, graceful shutdown.
- Write tests alongside code: unit, test vectors (reference/spec_vectors.py), and an integration test
  against the real compose service where the phase touches Kafka, ClickHouse, immudb or Wazuh.
- Stay inside your directories. Never edit another track's code.
- Commit in small logical steps with clear messages.

When done:
1. Run the phase's acceptance checks and paste the outputs.
2. Write docs/plan/reports/<PHASE_ID>.md using templates/PHASE_REPORT.md.
3. Execute templates/PLAN_SYNC.md exactly.
4. Summarize: what's done, what's deferred, what the human must verify by eye.
```
