# Veyra

**Any log, however messy, is sealed as evidence the moment it arrives, normalized deterministically,
delivered to your SIEM, and traceable byte-for-byte back to its source.**

Veyra is an air-gapped **log pre-processing framework** built for Smart India Hackathon problem
SIH26156 (NTRO). It sits in front of a SIEM such as Wazuh and:

- **seals** every raw log as tamper-evident evidence before anything parses it;
- **normalizes** it into [OCSF](https://ocsf.io) with a lineage extension (`ulpf`), grading how well it understood each line (tier 1 to 4) and never dropping one;
- **learns new formats** through Log Contracts that a local AI helps draft, with automatic checks and two-person approval;
- **proves** any event is untouched, and locates tampering if it isn't.

It is a pre-processor, not a SIEM: detection stays with the SIEM it feeds.

---

## Quick start

You need **Docker** and **git**. Nothing else is installed on your machine; every build and tool
runs in a container.

**Windows** (PowerShell, with Docker Desktop):

```powershell
git clone https://github.com/arnavmahajan630/Veyra
cd Veyra
.\veyra.ps1
```

**Linux, WSL or macOS:**

```bash
git clone https://github.com/arnavmahajan630/Veyra
cd Veyra
./veyra.sh
```

That single command:

1. checks the machine and picks a hardware profile (laptop or workstation);
2. clones the Log Contract registry next to the repo (`../contracts-repo`);
3. writes the runtime settings (`.env.runtime`) and data folders;
4. builds the Veyra image and the web console;
5. starts Kafka, ClickHouse, immudb, the edge collectors, Caddy and a Kafka UI, and creates the topics;
6. starts Wazuh (workstation profile), with certificates and security set up automatically;
7. asks which AI should draft contracts (see below), starts that model server (on the GPU when Docker can see one) and downloads the model;
8. starts every Veyra service;
9. runs a smoke check: every service answers, and a syslog line comes out the far end as OCSF, delivered to Wazuh's input file;
10. prints the URLs and sign-ins.

The first run downloads images and a model, so allow **10 to 25 minutes**. Later runs take about a minute.
Every stage is safe to re-run.

Then:

```bash
./veyra.sh demo      # guided, narrated walkthrough of every feature (about 5 minutes)
./veyra.sh load      # Kafka and pipeline throughput test
```

On Windows, replace `./veyra.sh` with `.\veyra.ps1`.

## What you get

| What | Where | Sign in |
|---|---|---|
| Web console | http://localhost:8080 | `admin@veyra`, `author@maha` or `approver@veyra`; password `veyra-demo` |
| Kafka UI (topics, lag, live rates) | http://localhost:8085 | none (read-only) |
| Control API (OpenAPI) | http://localhost:8000/docs | same as the console |
| Evidence API (OpenAPI) | http://localhost:8100/docs | none |
| HTTP push, Splunk-HEC compatible | http://localhost:8088 | a per-source API key |
| Syslog in | UDP/TCP 5514/5515 (DMZ zone), 5524/5525 (core zone) | the source's registered host |
| Wazuh dashboard (workstation profile) | https://localhost:8443 | `admin` / `admin` |

## Commands

| Command | What it does |
|---|---|
| `up` (default) | Set up and start everything, then smoke-check it |
| `demo` | Guided walkthrough; `--auto` runs without pauses, `--beat N` runs one beat |
| `load` | Stage A: Kafka alone (`--events N`, default 100M). Stage B: the real pipeline (`--pipeline N`, default 1M). `--skip-kafka`, `--skip-pipeline`, `--force` |
| `status` | Container list and a health check |
| `logs [service]` | Follow logs, e.g. `logs normalizer` |
| `reset` | Reset the control plane to the seeded demo world |
| `down` | Stop everything; data is kept |
| `wipe` | Stop everything and delete all data (asks first) |
| `doctor` | Check Docker, resources and ports |

Options for `up`:

| Option | Default |
|---|---|
| `--profile laptop\|workstation` | Auto: `workstation` when Docker has at least 48 GB RAM and 12 CPUs |
| `--wazuh` / `--no-wazuh` | On for workstation, off for laptop |
| `--drafter ollama\|laya` | Asks on the first run, then remembers. With `--yes` or no terminal: `ollama` |
| `--laya` | Short for `--drafter laya` |
| `--decision-model NAME` | `laya:en`. The decision model to pull when the drafter is `laya` |
| `--no-llm` | The AI model is on. Without it, drafts come from saved answers or the rules drafter |
| `--model NAME` | The profile's model: `qwen2.5:3b` (laptop), `qwen2.5:14b` (workstation) |
| `--yes` | Don't ask before system changes, and don't ask which drafter |

### Choosing the AI drafter

When a new log format shows up, an AI proposes how its values map to fields; checks and a second
person's approval follow either way. Two kinds of model can do it:

| | `ollama` (default) | `laya` |
|---|---|---|
| What it is | An LLM that writes the draft, limited to fixed lists of answers | A decision model that picks from the same lists and gives a probability for each |
| Accuracy (27 test cases, RTX 4050) | Better: recall 0.45 to 0.59 | Lower: recall 0.33; good at the event type and outcome, leaves more values unmapped |
| Time per draft | 1 to 2 s on a GPU; too slow on a CPU, so the laptop profile uses saved drafts | About 0.1 s on a GPU and 1 to 2 s on a CPU, so it drafts live on every profile |
| Download | A few GB | Under 1 GB |

Switch at any time by re-running `up` with the other value: `./veyra.sh up --drafter laya`. The
numbers are in [`docs/plan/reports/C4.md`](docs/plan/reports/C4.md).

On Windows, `.\veyra.ps1 -Via wsl` or `-Via toolbox` forces a route (see below).

## Profiles

Sizes, partitions, limits and models all come from `profiles/<name>.env`; nothing is hard-coded.

| Profile | For | Highlights |
|---|---|---|
| `laptop` | 16 GB+ RAM | 1 normalizer, 3 partitions per topic, small heaps, Wazuh off, `qwen2.5:3b` |
| `workstation` | 64 to 128 GB RAM, 16+ cores, NVIDIA GPU | 6 normalizers for load tests, 12 partitions, Wazuh on, `qwen2.5:14b` on the GPU |

Put personal overrides in `.env.local` (git-ignored). It is applied after the profile.

## How it works

```
devices --syslog--> edge collectors (Vector) --+
orgs ----HTTP-----> ingest gateway (API keys) -+--> Kafka raw.* --+--> normalizer --> norm.* --> router --> Wazuh
                                                                   +--> archiver ----> sealed vault --> integrity (Merkle + Ed25519)
                                                                   +--> lineage indexer --> ClickHouse --> evidence API
control API (contracts, keys, four-eyes) --> Kafka `control` --> normalizer / gateway;  drift worker <-- dlq
```

Every service talks over Kafka topics or REST, behind one origin on port 8080 (Caddy). For the
plain-English and technical explanations, read the **Veyra, explained** doc and
[`docs/DEMO_GUIDE.md`](docs/DEMO_GUIDE.md). The design and decision log live in
[`docs/plan/`](docs/plan/README.md).

**What is built today** (2 Oct 2026):
- Ingestion, normalization (tiers 1 to 4, byte offsets, shadow and replay), the router to Wazuh and a masked partner feed, the control plane, drift and the AI drafter are built.
- The whole console is built and reads live data: Overview, Sources, Onboard, Contracts, Drift, Lineage, Evidence, Delivery, Audit, and the hidden demo panel at `/demo`.
- The evidence side runs as working prototypes: the archiver, the Merkle and Ed25519 integrity service, the evidence API and the tamper lab. **Verify checks 7 of its 8 steps** — the eighth anchors the signed root in immudb and is declared not implemented; it reports itself as such everywhere rather than showing a tick it has not earned.
- The demo engine drives the whole 3-minute script (`make demo-reset`, `demo-preflight`, `demo-stage N=3`, `demo-auto`), and the `Shift+1`..`Shift+6` hotkeys work from any console page.
- Sealed segments are mode `0444` and hash-chained, but not `chattr +i`: the vault resists an accidental edit, and a signed Merkle root outside it is what catches a deliberate one.
- `make cp1` to `make cp4` run the integration checkpoints that gate the demo; `docs/plan/06_STATUS_BOARD.md` records each run.

[`docs/DEMO_GUIDE.md`](docs/DEMO_GUIDE.md) has the full scope matrix.

## Windows notes

`veyra.ps1` runs the same `veyra.sh` in one of two ways:

- **Through WSL**, when a WSL distro can reach Docker (Docker Desktop, Settings, Resources, WSL integration). This is fastest.
- **In a small toolbox container** otherwise. It needs only Docker Desktop.

When the repo sits on a Windows drive, Kafka's and ClickHouse's data go into Docker volumes, because
bind mounts across the Windows filesystem are too slow under load. For the fastest load tests,
clone the repo inside WSL (`~/Veyra`) and run `./veyra.sh` there.

The launcher also rewrites shell scripts and container configs to LF line endings when git
checked them out as CRLF.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Docker is not running" | Start Docker Desktop, or `sudo systemctl start docker`, and re-run |
| A port is busy (8080, 8088, 9092 ...) | `./veyra.sh doctor` shows which. The console moves to 8081 automatically; stop whatever holds the other ports |
| Wazuh indexer keeps restarting | It needs `vm.max_map_count` at least 262144. `up` offers to set it; on Linux it resets at reboot |
| Cloning `contracts-repo` fails | It may be private. Sign in to GitHub when git asks, or clone it next to this folder yourself |
| The first build is slow | Expected: images, the console build and the model download (a few GB) happen once |
| Docker runs out of memory | Give Docker Desktop more memory (Settings, Resources, or `.wslconfig`). The laptop profile needs about 8 GB; the workstation profile with Wazuh about 40 GB |
| Something is wrong and you want to start over | `./veyra.sh wipe`, then `./veyra.sh up` |
| A stage failed | Re-run the same command; every stage is idempotent. `./veyra.sh logs <service>` shows why |

## Repository layout

| Path | What's in it |
|---|---|
| `veyra.sh`, `veyra.ps1` | The one-command entry points |
| `services/` | The runnable services: ingest_gateway, normalizer, router, archiver, integrity, lineage_indexer, evidence_api, demo_engine, control_api, drift_worker |
| `packages/` | Shared libraries: `veyra_common`, `veyra_engine` (the normalizer), `veyra_evidence`, `veyra_lineage`, `veyra_contracts` (contracts + the AI drafter) |
| `console/` | The React web console |
| `compose/`, `docker/`, `profiles/` | Containers, images and per-machine settings |
| `edge/` | Vector edge collector configs and the source inventory |
| `wazuh/` | Wazuh add-ons: where Veyra's output is read and the custom rules |
| `demo/` | Sample logs and senders |
| `tools/` | Smoke check, guided demo, load generator, tamper lab, ledger audit, benches |
| `docs/` | [`DEMO_GUIDE.md`](docs/DEMO_GUIDE.md) (steps and scope), `plan/` (design, contracts, status, reports) |
| `../contracts-repo` | The Log Contract registry: a separate git repo, cloned beside this one |

For development (unit tests, lint, per-service runs), see the `Makefile` (`make help`) and [`CLAUDE.md`](CLAUDE.md).
