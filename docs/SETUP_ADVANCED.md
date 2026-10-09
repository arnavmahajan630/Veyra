# Veyra setup script: advanced guide

Everything `./veyra.sh` (Linux, WSL, macOS) and `.\veyra.ps1` (Windows) can do, with every option.
For the short version, see the [README](../README.md). For what the demo shows and how deep each
feature goes, see [DEMO_GUIDE.md](DEMO_GUIDE.md).

The examples use `./veyra.sh`. On Windows, type `.\veyra.ps1` instead; every argument is the same.

**Contents:** [Commands](#1-commands) · [Options for `up`](#2-options-for-up) ·
[The ten stages](#3-what-up-does-stage-by-stage) · [The AI drafter](#4-the-ai-drafter) ·
[Settings files](#5-settings-what-wins-over-what) · [Windows](#6-windows-the-two-routes) ·
[Demo](#7-the-guided-demo) · [Load test](#8-the-load-test) ·
[Stop, reset, wipe](#9-stop-reset-wipe) · [Recipes](#10-recipes) ·
[What it leaves on the machine](#11-what-it-leaves-on-the-machine) ·
[Without internet](#12-running-without-internet) · [Troubleshooting](#13-troubleshooting) ·
[What has been tested](#14-what-has-been-tested)

---

## 1. Commands

```
./veyra.sh [command] [options]
```

| Command | What it does | Needs the stack set up |
|---|---|---|
| `up` (default) | Sets up, builds, starts and smoke-checks everything | no |
| `demo` | Guided walkthrough of five beats | yes |
| `load` | Kafka throughput test, then a real-pipeline test | yes |
| `status` | Lists the containers, then runs the health checks | yes |
| `logs [service ...]` | Follows logs (last 200 lines first). No name means every service | yes |
| `reset` | Returns the demo world to its seeded state; the stack keeps running | yes |
| `down` (or `stop`) | Stops every container; data is kept | yes |
| `wipe` | Stops everything and deletes all data; asks first | no |
| `doctor` | Checks Docker, memory, CPUs, disk, GPU and which ports are busy. Changes nothing | no |
| `help` | Prints the commands and options | no |

"Needs the stack set up" means `up` must have run once in this folder. Otherwise the command stops
with "the stack has not been set up here yet".

Anything the script doesn't recognise is passed on: to the walkthrough for `demo`, to the load
generator for `load`, and to `docker compose logs` for `logs`.

## 2. Options for `up`

| Option | What it does | Default |
|---|---|---|
| `--profile NAME` | Uses `profiles/NAME.env` for sizes, limits and models. `laptop`, `workstation` or `mac` | `workstation` when Docker has at least 48 GB RAM and 12 CPUs, otherwise `laptop` |
| `--wazuh` | Starts the Wazuh SIEM (indexer, manager, dashboard) | on for `workstation` |
| `--no-wazuh` | Leaves Wazuh out | off for every other profile |
| `--drafter ollama` | An Ollama LLM drafts contracts | asked on the first run in a terminal, then remembered; `ollama` with `--yes` or no terminal |
| `--drafter laya` or `--laya` | The Laya decision model drafts contracts | |
| `--decision-model NAME` | Which decision model `laya` pulls and uses | `laya:en` |
| `--model NAME` | Which Ollama model to pull and use | the profile's `VEYRA_LLM_MODEL` |
| `--no-llm` | No model server at all; drafts come from saved answers or the rules drafter | a model is used |
| `--yes` or `-y` | Never asks: no drafter question, and yes to the Wazuh kernel setting and to `wipe` | asks |

Notes:

- **The profile is checked against Docker, not the host.** On Docker Desktop that is the memory and
  CPUs given to Docker (Settings, Resources, or `.wslconfig`), which is usually less than the machine has.
- **Choices are remembered** in `data/state/veyra.env` (profile, Wazuh, drafter, models), so
  `status`, `demo`, `load` and `down` use the same compose files `up` did. Only the drafter choice
  is reused by a later `up`; the profile and Wazuh are detected again unless you pass the flags.
- **Re-running `up` with different options changes the running stack.** Containers that are no
  longer wanted are removed (for example Ollama when you switch to Laya); data is kept.

## 3. What `up` does, stage by stage

| # | Stage | What happens | Safe to repeat because |
|---|---|---|---|
| 1 | Checking this machine | Docker, Compose v2 and git are present; reads RAM, CPUs and free disk; picks the profile, Wazuh and the drafter; probes for an NVIDIA GPU | it only reads |
| 2 | Log Contract registry | Clones `contracts-repo` next to this folder | it is skipped when the folder exists |
| 3 | Settings and data folders | Writes `.env.runtime`; creates `data/...` | the file is regenerated every time |
| 4 | Building | Builds `veyra/python`, the console (in a Node container), the edge configs, the evidence keys | Docker's cache; the console is skipped unless its sources changed; keys are created once |
| 5 | Infrastructure | Starts Kafka, ClickHouse, immudb, both edge collectors, Caddy and the Kafka UI; creates the topics | running containers are left alone; existing topics are kept |
| 6 | Wazuh | Sets `vm.max_map_count` (asks), makes certificates once, starts Wazuh, initialises its security once | marker files under `data/` |
| 7 | AI drafter model | Starts Ollama or Ollaya, downloads the model, loads it | the model is kept in a Docker volume |
| 8 | Veyra services | Starts every service and waits until each answers | Compose only restarts what changed |
| 9 | Smoke check | Health of every service; all topics; sign-in; a syslog line through to the Wazuh sink; evidence indexed, signed and audited; a push event with a newly issued key; the DMZ listener | it sends its own probe events |
| 10 | Ready | Prints the URLs, sign-ins and the drafter in use | |

If stage 9 fails, `up` prints `FAILED` after the summary and exits with status 1. A stage that
stops with an error names the stage; re-run the same command to continue.

**The GPU probe** runs `nvidia-smi` in a tiny container with `--gpus all`. If that works, the model
server gets the GPU. If not, it runs on the CPU and stage 1 says so.

**Ports.** Only the console moves by itself (8080 to 8081 when 8080 is busy). Every other port must
be free: 8000, 8085, 8088, 8100, 8123, 8201 to 8206, 8300, 9000, 9092, 29092, 3322, 5433, 9497,
5514/udp, 5515, 5524/udp, 5525, and with Wazuh 8443, 9200, 1514, 1515, 514/udp, 55000. `doctor` lists the busy ones.

## 4. The AI drafter

When a new log format appears, a model proposes which value fills which field. Every draft is then
checked by rules and approved by a second person, whichever model made it.

| | `--drafter ollama` | `--drafter laya` | `--no-llm` |
|---|---|---|---|
| Server | Ollama (`veyra-ollama`) | Ollaya (`veyra-ollaya`) | none |
| Model | the profile's LLM, or `--model` | `laya:en`, or `--decision-model` | none |
| How it answers | Writes the draft, limited to fixed lists of answers | Picks from the same lists and gives a probability for each | Saved drafts, else fixed rules |
| Measured here (27 cases, RTX 4050) | recall 0.45 (`qwen2.5:3b`) to 0.59 (`llama3.2:3b`); 1.5 s a draft | recall 0.33; 0.09 s a draft on the GPU, about a second on the CPU | n/a |
| Download | 2 to 9 GB | under 1 GB | none |
| Drafts live on the `laptop` profile | no, see below | yes | no |

**The laptop profile and Ollama.** The laptop profile sets `VEYRA_LLM_MODE=cache`, because an LLM on
a laptop CPU takes about a minute per draft. With Ollama there, the model is downloaded and loaded,
but a draft comes from a saved answer or the rules. If your laptop has an NVIDIA GPU and you want
live LLM drafts, put this in `.env.local` and re-run `up`:

```
VEYRA_LLM_MODE=live_then_cache
```

With Laya the script sets that mode itself on every profile.

**Modes** (`VEYRA_LLM_MODE`): `live` asks the model every time; `live_then_cache` asks the model
and saves the answer, and uses the saved answer if the model fails; `cache` only uses saved answers;
`heuristic` only uses the rules. A draft the model can't produce always falls back to the rules.

**Other models.**

```bash
./veyra.sh up --drafter ollama --model llama3.2:3b          # the most accurate one measured on the laptop
./veyra.sh up --drafter laya --decision-model winnow:e4b    # a larger decision model: an 8 GB download, not tried here
```

Any name the server can pull works. Only `qwen2.5:3b`, `llama3.2:3b` and `laya:en` have been
measured; the numbers are in [`plan/reports/C4.md`](plan/reports/C4.md).

**Switching** is a re-run: `./veyra.sh up --drafter laya`, later `./veyra.sh up --drafter ollama`.
Each model stays in its Docker volume, so switching back downloads nothing.

**If the download fails**, `up` carries on in cache mode and says so. Re-run `up` when the network is back.

## 5. Settings: what wins over what

`up` writes `.env.runtime` from three sources, and a later line wins:

1. `profiles/<profile>.env`, the hardware profile (in git).
2. `.env.local`, your own overrides (not in git; create it next to `veyra.sh`).
3. The script's own lines, written last.

Never edit `.env.runtime`; it is regenerated on every `up`.

**What the script always writes** (so `.env.local` cannot change these):

| Setting | Value |
|---|---|
| `VEYRA_UID`, `VEYRA_GID`, `VEYRA_DOCKER_GID` | your user and the Docker socket's group, so files under `data/` belong to you |
| `VEYRA_CONSOLE_PORT` | 8080, or 8081 when 8080 is busy |
| `VEYRA_DEMO_MODE=1` | enables the demo user switch and the demo panel |
| `VEYRA_SEGMENT_MAX_SECONDS=20` | evidence seals within the demo |
| `VEYRA_VAULT_CHATTR=0` | sealed files are read-only (`0444`) rather than immutable |
| `VEYRA_LLM_MODEL` | the profile's model, or `--model` |
| `VEYRA_OLLAMA_URL=http://ollama:11434` | with the `ollama` drafter |
| `VEYRA_LLM_BACKEND=decision`, `VEYRA_DECISION_URL`, `VEYRA_DECISION_MODEL`, `VEYRA_LLM_MODE=live_then_cache` | with the `laya` drafter |
| `VEYRA_LLM_MODE=cache` | with `--no-llm`, or when a model download fails |
| `VEYRA_WAZUH_MODE=remote` | when Wazuh is off |

**Useful things to put in `.env.local`:**

| Setting | Why |
|---|---|
| `VEYRA_LLM_MODE=live_then_cache` | live LLM drafts on a laptop with a GPU (section 4) |
| `VEYRA_LLM_TIMEOUT_S=40` | give a slow model longer per draft |
| `VEYRA_DECISION_MIN_PROBABILITY=0.4` | how sure the decision model must be to keep a mapping (default 0.5) |
| `VEYRA_KAFKA_UI_PORT=8090` | move the Kafka UI off 8085 |
| `VEYRA_NORMALIZER_REPLICAS=4` | how many normalizers the load test starts (up to 6) |
| `VEYRA_MEM_PY_SERVICE=384m` | more memory per Python service |

Every setting and its default is in `packages/veyra_common/src/veyra_common/settings.py`; the
per-profile values are in `profiles/`.

**Environment variables the script itself reads** (set them in the shell before running):

| Variable | Why |
|---|---|
| `VEYRA_CONTRACTS_REPO_DIR` | the contracts registry lives somewhere other than `../contracts-repo` |
| `VEYRA_CONTRACTS_URL` | clone the registry from another URL (a fork or a mirror) |

## 6. Windows: the two routes

`veyra.ps1` first starts Docker Desktop if it isn't running, rewrites scripts and configs to LF line
endings, and clones the contracts registry. Then it runs `veyra.sh` one of two ways:

| Route | When | How |
|---|---|---|
| WSL | a WSL distro can run `docker info` (Docker Desktop, Settings, Resources, WSL integration) | `wsl -d <distro> bash ./veyra.sh ...` |
| Toolbox | otherwise | a small container (`veyra/toolbox:1`) with the Docker CLI, bash and git, with this folder's parent mounted |

Force one with `-Via`, which is the launcher's only option of its own:

```powershell
.\veyra.ps1 up -Via toolbox
.\veyra.ps1 up --profile workstation --wazuh -Via wsl
```

Things to know:

- **Data location.** When the repo is on a Windows drive, Kafka's and ClickHouse's data go into
  Docker volumes instead of `data/`, because bind mounts across the Windows filesystem are slow.
  Everything else stays under `data/`.
- **For load tests**, clone the repo inside WSL (`~/Veyra`) and run `./veyra.sh` there. That avoids
  the Windows filesystem completely.
- **GPU.** Docker Desktop passes an NVIDIA GPU through WSL 2. Nothing extra to install beyond the
  Windows NVIDIA driver.

## 7. The guided demo

```bash
./veyra.sh demo                 # pauses after each beat; press Enter
./veyra.sh demo --auto          # no pauses
./veyra.sh demo --beat 5        # one beat
./veyra.sh demo --beat 1 --beat 2
./veyra.sh demo --no-reset      # keep the current state
```

| Beat | Shows |
|---|---|
| 1 | Syslog and CEF in, OCSF out, with byte offsets |
| 2 | Messy and binary logs: tier 3 and tier 4, nothing dropped |
| 3 | Onboarding a new source: draft, two-person approval, a per-source key, HTTP push |
| 4 | A new log shape: drift, draft, approve, promote, replay as revision 2 |
| 5 | Evidence: verify, tamper, detect, restore, ledger audit |

Unless you pass `--no-reset`, a run that includes beat 3 or 4 first resets the demo world, so a
second run behaves like the first. That takes about a minute. Each step prints PASS or FAIL
and the run ends with a tally; the exit status is non-zero if anything failed.

The drafter used in beats 3 and 4 is whatever `up` configured.

## 8. The load test

```bash
./veyra.sh load                                         # 100M records to Kafka, then 1M events through the pipeline
./veyra.sh load --events 1000000000 --skip-pipeline     # Kafka only: one billion records
./veyra.sh load --skip-kafka --pipeline 5000000         # pipeline only: five million events
./veyra.sh load --events 5000000 --pipeline 200000      # a small run for a laptop
```

| Option | What it does | Default |
|---|---|---|
| `--events N` | Stage A: records sent by Kafka's own producer benchmark | 100,000,000 |
| `--record-size B` | Stage A: bytes per record | 200 |
| `--pipeline N` | Stage B: real events through the normalizers | 1,000,000 |
| `--skip-kafka` | Skip Stage A | |
| `--skip-pipeline` | Skip Stage B | |
| `--force` | Run even if it would use more than 70% of the free disk | |
| `--workers N` | Stage B: producer processes | half the CPUs, 2 to 16 |
| `--interval S` | Stage B: seconds between progress lines | 5 |
| `--timeout S` | Stage B: stop measuring after this long | 3600 |
| `--stall S` | Stage B: stop with an error when nothing has been normalized for this long and events are still queued | 300 |
| `--measure-only` | Stage B: send nothing, only measure what is flowing | |

- **Stage A** creates a topic `bench.load`, produces to it with full durability (`acks=all`,
  idempotent, zstd), reads it back, and deletes it. It measures Kafka on this machine, not Veyra.
- **Stage B** sends real, envelope-stamped events to `raw.*` and measures how fast normalized
  events come out. It starts extra normalizers up to the profile's `VEYRA_NORMALIZER_REPLICAS`
  (six on `workstation`). They keep running until `down`.
- **The disk guard** estimates the space needed and refuses to start above 70% of what is free.
  Stage A needs at most `events x record-size` bytes before compression.
- **After a load test, run `./veyra.sh reset` before a demo.** The load leaves a large backlog for
  the services that follow the normalizer, and the demo's own events would queue behind it.

Watch both stages live in the Kafka UI at http://localhost:8085.

## 9. Stop, reset, wipe

| Command | Containers | Demo data (events, contracts, keys) | Models and images | Use it when |
|---|---|---|---|---|
| `down` | stopped | kept | kept | you are done for now |
| `reset` | keep running | back to the seeded state | kept | before a demo, after a load test, or when old events get in the way |
| `wipe` | removed | deleted, with the Docker volumes of the stack as it last ran (including that drafter's downloaded model) | images kept | you want a completely fresh start |

After `down`, `up` brings the same stack back. After `wipe`, `up` starts from nothing and
downloads the model again.

## 10. Recipes

**The workstation, everything on:**
```bash
./veyra.sh up --profile workstation --wazuh --drafter ollama
```

**A laptop, smallest footprint:**
```bash
./veyra.sh up --profile laptop --no-wazuh --no-llm
```

**A laptop with the fast drafter:**
```bash
./veyra.sh up --laya
```

**Unattended (a script or CI), no questions:**
```bash
./veyra.sh up --yes --drafter ollama && ./veyra.sh demo --auto
```
Both commands exit non-zero on failure.

**Wazuh on a laptop** (needs about 3.5 GB more memory for Docker):
```bash
./veyra.sh up --wazuh
```

**A Mac:**
```bash
./veyra.sh up --profile mac --drafter laya
```
Docker on macOS has no GPU, so a model in a container runs on the CPU. Laya is usable there; an
LLM is slow. This route has not been tested.

**See what is running and whether it is healthy:**
```bash
./veyra.sh status
./veyra.sh logs control-api normalizer
```

**Before a rehearsal:**
```bash
./veyra.sh up && ./veyra.sh reset && ./veyra.sh demo --auto
```

## 11. What it leaves on the machine

| What | Where | Removed by |
|---|---|---|
| Runtime settings | `.env.runtime` | `wipe` |
| Remembered choices | `data/state/veyra.env` | `wipe` |
| Events, vault, keys, sinks, saved drafts, Wazuh certificates | `data/` | `wipe` |
| The console build | `console/dist/` | delete the folder |
| The contracts registry | `../contracts-repo` | delete the folder |
| Docker volumes: `veyra_ollama_models`, `veyra_ollaya_models`, the Wazuh volumes, and on Windows drives `veyra_kafka_data` and `veyra_clickhouse_data` | Docker | `wipe` |
| Docker volume `veyra_console_node_modules` (the console's build dependencies) | Docker | `docker volume rm veyra_console_node_modules` |
| Docker images: `veyra/python`, `veyra/toolbox` (Windows), and the pulled ones | Docker | `docker image rm`, or `docker image prune` |

Nothing is installed on the host outside Docker and these folders.

## 12. Running without internet

Veyra is built to run air-gapped. The first `up` is the only step that needs the internet. It downloads:

- the container images: Kafka, ClickHouse, immudb, Vector, Caddy, the Kafka UI, Ollama or Ollaya,
  Wazuh (when on), and small helper images (Python, Node, Alpine, Debian);
- the Python and console build dependencies;
- the AI model;
- the contracts registry from GitHub.

After that, `demo`, `load`, `status`, `reset`, `down` and `up` on unchanged code use only what is
already on the machine, and no service calls out at runtime. Two limits:

- **Changing the code can need the network again.** A changed dependency rebuilds the Python image,
  and changed console sources rebuild the console; both fetch packages.
- **A complete run with the network off has not been tested.** Do one before relying on it.

To move a prepared machine's images to another machine, use `docker save` and `docker load`.

## 13. Troubleshooting

| Symptom | What to do |
|---|---|
| "stopped at stage N" | Re-run the same command. If it stops again, `./veyra.sh logs <service>` for the service that stage starts |
| `FAILED` at the end of `up` | The smoke check lists what failed. `./veyra.sh status` re-runs the health part |
| A service is "slow to start" on a small machine | Give Docker more memory. Below 12 GB the script warns; the laptop profile still runs in about 8 GB without Wazuh |
| The drafter question doesn't appear | It is asked only on the first `up` in a terminal. Pass `--drafter` to change the remembered answer |
| "no NVIDIA GPU visible to Docker" on a machine that has one | Update the NVIDIA driver; on Linux install the NVIDIA Container Toolkit. Test with `docker run --rm --gpus all debian:bookworm-slim nvidia-smi` |
| The model "did not warm up" | The first draft will be slow while it loads. `./veyra.sh logs ollama` or `logs ollaya` shows why |
| A port is in use | `./veyra.sh doctor` lists the busy ports. Stop what holds them; only the console port moves by itself |
| Wazuh's indexer keeps restarting | It needs `vm.max_map_count` of at least 262144. Re-run `up --wazuh` and answer yes; on Linux the value resets at reboot |
| Cloning the contracts registry fails | Sign in to GitHub when git asks, or clone it yourself next to this folder, or set `VEYRA_CONTRACTS_URL` |
| The load test refuses to start | The disk guard. Lower `--events` or `--pipeline`, free some disk, or pass `--force` |
| The load test shows `0` in the rate column for a while | Kafka paused under the burst. A normalizer whose transaction is stuck exits after `VEYRA_KAFKA_TXN_TIMEOUT_MS` (2 minutes) and Docker restarts it, so the rate comes back. If it stays at 0, the test stops after `--stall` seconds; `./veyra.sh logs normalizer` and `docker logs veyra-kafka` show why |
| A demo beat times out on a stack that has been up for days | `./veyra.sh reset` |
| Beat 4 fails with "no events to replay", or lineage searches come back empty now and then | ClickHouse is at its memory cap (`./veyra.sh logs evidence-api` shows `MEMORY_LIMIT_EXCEEDED`). Give Docker more memory, raise `VEYRA_MEM_CLICKHOUSE` and `VEYRA_CH_MAX_MEMORY` in `.env.local` (the laptop profile uses `1g` and `800m`), and run `up` again. Seen with Docker at 7 GB |
| Windows: "$'\r': command not found" | Run through `.\veyra.ps1`, which fixes line endings, rather than calling `veyra.sh` directly from a CRLF checkout |
| Windows: the launcher picks the wrong route | Force it with `-Via wsl` or `-Via toolbox` |
| Files under `data/` are owned by root | Something ran as root. `sudo chown -R "$USER" data`, then `up` again |

## 14. What has been tested

On one machine: Windows 11, Docker Desktop with 7 GB and 12 CPUs, an RTX 4050 laptop GPU, through
`veyra.ps1` and the toolbox route.

| | Result |
|---|---|
| `up` on the laptop profile, with `--drafter laya` and with `--drafter ollama` | 30 of 30 smoke checks each; switching between them removes the other model server |
| `demo --auto` | all five beats, 17 of 17 checks; the reset before it takes 40 to 55 s |
| `up --wazuh` on the laptop profile | passed on an earlier version of the script |
| The drafter question | 13 cases through a pseudo-terminal |
| The CPU-only Ollaya image | drafts; about 0.75 s for three questions |
| `load` | small runs: 5M records in Stage A, 200k events in Stage B |

Not tested: the WSL route, native Linux, macOS, the `workstation` and `mac` profiles, a
billion-record load run, and a run with the network off.
