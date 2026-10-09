#!/usr/bin/env bash
# Veyra: one command to set up, run, demo and load-test the whole stack.
#
#   ./veyra.sh               same as `./veyra.sh up`
#   ./veyra.sh up            build and start everything, then smoke-check it
#   ./veyra.sh demo          guided end-to-end walkthrough (add --auto for no pauses)
#   ./veyra.sh load          Kafka + pipeline throughput test (see ./veyra.sh help)
#   ./veyra.sh status | logs [svc] | down | reset | wipe | doctor | help
#
# Needs only Docker (Compose v2) and git. Every Python / Node step runs in a container.
# On Windows, run .\veyra.ps1 instead; it runs this script through WSL or a toolbox container.
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
REPO="$(pwd)"
STATE_FILE="$REPO/data/state/veyra.env"
CONTRACTS_REPO="${VEYRA_CONTRACTS_REPO_DIR:-$REPO/../contracts-repo}"
CONTRACTS_URL="${VEYRA_CONTRACTS_URL:-https://github.com/arnavmahajan630/contracts-repo}"
OLLAMA_IMAGE="ollama/ollama:0.34.4"
KAFKA_IMAGE="apache/kafka:4.1.2"
NODE_IMAGE="node:25-bookworm-slim"
SERVICE_PROFILES=(a2 a3 a6 c1 c3 b1 b2 b3 b4 b7)

# ---------------------------------------------------------------- output
if [[ -t 1 ]]; then
  B=$'\033[1m'; D=$'\033[2m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; N=$'\033[0m'
else
  B=; D=; G=; Y=; R=; C=; N=
fi
STAGE=0
STAGES=10
stage() { STAGE=$((STAGE + 1)); printf '\n%s[%2d/%d]%s %s%s%s\n' "$C" "$STAGE" "$STAGES" "$N" "$B" "$*" "$N"; }
info() { printf '       %s\n' "$*"; }
ok() { printf '       %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '       %s!%s %s\n' "$Y" "$N" "$*"; }
die() { printf '\n%s✗ %s%s\n' "$R" "$*" "$N" >&2; exit 1; }
trap 'die "stopped at stage $STAGE (line $LINENO). Re-run the same command: every stage is safe to repeat."' ERR

# ---------------------------------------------------------------- options
CMD="${1:-up}"
[[ $# -gt 0 ]] && shift
PROFILE="" WAZUH="" LLM="" MODEL="" YES=0 GPU="" FASTDATA="" PASSTHRU=()
# Which model drafts contracts: ollama (an LLM that writes JSON) or laya (a decision model
# that answers multiple-choice questions). Empty = ask, or reuse the last run's choice.
DRAFTER="" DRAFTER_FLAG=0 DECISION_MODEL=""
LOAD_EVENTS=100000000 LOAD_PIPELINE=1000000 LOAD_SIZE=200 LOAD_FORCE=0 LOAD_SKIP_KAFKA=0 LOAD_SKIP_PIPELINE=0

usage() {
  sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
  cat <<EOF

Options for up:
  --profile laptop|workstation   hardware profile (default: auto-detected from Docker's RAM/CPUs)
  --wazuh | --no-wazuh           include the Wazuh SIEM (default: on for workstation, off for laptop)
  --drafter ollama|laya          which AI drafts contracts (default: asks once, then remembers)
                                   ollama  an LLM writes the draft: most accurate here, 1-2 s a draft
                                   laya    a decision model picks from fixed options: about 0.1 s a
                                           draft and under 1 GB, but it leaves more fields unmapped
  --laya                         same as --drafter laya
  --decision-model NAME          decision model to pull with laya (default: laya:en)
  --no-llm                       skip the AI model; drafts use saved answers / rules
  --model NAME                   Ollama model to pull (default: the profile's VEYRA_LLM_MODEL)
  --yes                          do not ask before system changes (vm.max_map_count, wipe)

Options for load:
  --events N        Stage A: records for Kafka's own producer benchmark   (default 100,000,000)
  --pipeline N      Stage B: real envelopes through the normalizers       (default 1,000,000)
  --record-size B   Stage A record size in bytes                          (default 200)
  --skip-kafka | --skip-pipeline | --force (ignore the disk-space guard)

Options for demo:   --auto  --beat N  --no-reset
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --wazuh) WAZUH=1; shift ;;
    --no-wazuh) WAZUH=0; shift ;;
    --drafter) DRAFTER="$2"; DRAFTER_FLAG=1; shift 2 ;;
    --laya) DRAFTER=laya; DRAFTER_FLAG=1; shift ;;
    --decision-model) DECISION_MODEL="$2"; shift 2 ;;
    --no-llm) LLM=0; shift ;;
    --model) MODEL="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --events) LOAD_EVENTS="$2"; shift 2 ;;
    --pipeline) LOAD_PIPELINE="$2"; shift 2 ;;
    --record-size) LOAD_SIZE="$2"; shift 2 ;;
    --force) LOAD_FORCE=1; shift ;;
    --skip-kafka) LOAD_SKIP_KAFKA=1; shift ;;
    --skip-pipeline) LOAD_SKIP_PIPELINE=1; shift ;;
    *) PASSTHRU+=("$1"); shift ;;
  esac
done

# ---------------------------------------------------------------- helpers
have() { command -v "$1" >/dev/null 2>&1; }
confirm() { [[ $YES == 1 ]] && return 0; read -r -p "       $1 [y/N] " a; [[ $a =~ ^[Yy] ]]; }
env_value() { grep -E "^$1=" "$2" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r' || true; }
# Env files without comments, blanks or Windows line endings (a CRLF checkout adds "\r").
env_lines() { tr -d '\r' <"$1" | grep -vE '^\s*(#|$)' || true; }
# What the reviewer typed to get here (veyra.ps1 sets it), for the hints we print.
SELF="${VEYRA_CMD:-./veyra.sh}"
human() { awk -v b="$1" 'BEGIN{s="B KB MB GB TB";split(s,u," ");i=1;while(b>=1024&&i<5){b/=1024;i++}printf "%.1f %s",b,u[i]}'; }

load_state() { [[ -f $STATE_FILE ]] && . "$STATE_FILE"; true; }
save_state() {
  mkdir -p "$(dirname "$STATE_FILE")"
  printf 'PROFILE=%s\nWAZUH=%s\nLLM=%s\nGPU=%s\nMODEL=%s\nFASTDATA=%s\nDRAFTER=%s\nDECISION_MODEL=%s\n' \
    "$PROFILE" "$WAZUH" "$LLM" "$GPU" "$MODEL" "$FASTDATA" "$DRAFTER" "$DECISION_MODEL" >"$STATE_FILE"
}

compose_files() {
  FILES=(-f compose/docker-compose.yml -f compose/docker-compose.ui.yml)
  [[ $WAZUH == 1 ]] && FILES+=(-f compose/docker-compose.wazuh.yml)
  # One model server, whichever drafts: Ollama for an LLM, Ollaya for a decision model.
  local server=ollama gpu=gpu
  [[ ${DRAFTER:-ollama} == laya ]] && { server=decision; gpu=decision-gpu; }
  [[ $LLM == 1 ]] && FILES+=(-f "compose/docker-compose.$server.yml")
  [[ $LLM == 1 && $GPU == 1 ]] && FILES+=(-f "compose/docker-compose.$gpu.yml")
  [[ ${FASTDATA:-0} == 1 ]] && FILES+=(-f compose/docker-compose.fastdata.yml)
  PROFILE_ARGS=()
  for p in "${SERVICE_PROFILES[@]}"; do PROFILE_ARGS+=(--profile "$p"); done
}
dc() { docker compose --env-file .env.runtime "${FILES[@]}" "${PROFILE_ARGS[@]}" "$@"; }
# One-off commands in the `tools` container (same image as the services, on veyra_net).
tools_run() {
  docker compose --env-file .env.runtime "${FILES[@]}" --profile tools run --rm --quiet-pull "$@" \
    2> >(grep -vE 'No services to build|Container veyra-tools-run|Creating|Created|Starting|Started|Waiting|Healthy|Running' >&2 || true)
}
run_tools() { tools_run -T tools "$@"; }
run_tools_nodeps() { tools_run -T --no-deps tools "$@"; }
run_tools_tty() { if [[ -t 0 && -t 1 ]]; then tools_run tools "$@"; else tools_run -T tools "$@"; fi; }
# Indent and trim command output; never fails the pipeline on its own (pipefail).
shown() { grep -vE '^\s*$' | tail -"${1:-4}" | sed 's/^/       /' || true; }

wait_healthy() { # container, seconds
  local name=$1 left=$2 status
  while ((left > 0)); do
    status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name" 2>/dev/null || echo missing)
    if [[ $status == healthy ]]; then printf '%70s\r' ''; return 0; fi
    printf '       %swaiting for %s (%s) %3ss%s\r' "$D" "$name" "$status" "$left" "$N"
    sleep 3; left=$((left - 3))
  done
  printf '\n'; return 1
}

docker_mem_gb() { docker info --format '{{.MemTotal}}' | awk '{printf "%d", $1/1024/1024/1024}'; }
docker_cpus() { docker info --format '{{.NCPU}}'; }
free_disk_bytes() { df -Pk "$REPO" | awk 'NR==2 {print $4 * 1024}'; }
# Free space where Kafka's data lives (a Docker volume on Windows drives, else ./data).
kafka_free_bytes() {
  if [[ ${FASTDATA:-0} == 1 ]]; then
    # Docker Desktop's volume reports its virtual disk's ceiling; the disk file itself grows
    # on the Windows drive, so the real limit is the smaller of the two.
    local vol host
    vol=$(docker run --rm -q -v veyra_kafka_data:/d alpine:3.20 df -Pk /d | awk 'NR==2 {print $4 * 1024}')
    host=$(free_disk_bytes)
    echo $((vol < host ? vol : host))
  else
    free_disk_bytes
  fi
}

# ---------------------------------------------------------------- stages of `up`
preflight() {
  stage "Checking this machine"
  have docker || die "Docker is not installed. Install Docker Desktop (Windows/macOS) or Docker Engine (Linux)."
  docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start Docker Desktop / the docker service and retry."
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is missing (the 'docker compose' plugin)."
  have git || die "git is not installed."
  local mem cpus disk
  mem=$(docker_mem_gb); cpus=$(docker_cpus); disk=$(free_disk_bytes)
  ok "Docker $(docker version --format '{{.Server.Version}}'), $(docker compose version --short) compose; Docker sees ${mem} GB RAM, ${cpus} CPUs; $(human "$disk") free here"
  ((disk < 20 * 1024 * 1024 * 1024)) && warn "less than 20 GB free disk; images and data need about 15 GB"

  if [[ -z $PROFILE ]]; then
    if ((mem >= 48 && cpus >= 12)); then PROFILE=workstation; else PROFILE=laptop; fi
    ok "profile: ${B}$PROFILE${N} (auto: ${mem} GB / ${cpus} CPUs; override with --profile)"
  else
    ok "profile: ${B}$PROFILE${N}"
  fi
  [[ -f profiles/$PROFILE.env ]] || die "no such profile: profiles/$PROFILE.env"
  ((mem < 12)) && warn "Docker has only ${mem} GB. Raise Docker Desktop's memory limit (Settings > Resources, or .wslconfig) to at least 12 GB."

  [[ -z $WAZUH ]] && { [[ $PROFILE == workstation ]] && WAZUH=1 || WAZUH=0; }
  if [[ $REPO == /mnt/[a-z]/* || $REPO == /run/desktop/mnt/host/* ]]; then
    FASTDATA=1
    info "this folder is on a Windows drive: Kafka and ClickHouse data go to Docker volumes (much faster)"
  else
    FASTDATA=0
  fi
  [[ -z $LLM ]] && LLM=1
  [[ -z $MODEL ]] && MODEL=$(env_value VEYRA_LLM_MODEL "profiles/$PROFILE.env")
  choose_drafter
  if [[ $LLM == 1 ]]; then
    # With --gpus the NVIDIA runtime (Linux toolkit or Docker Desktop's WSL2 GPU support)
    # injects nvidia-smi into any glibc image, so a small one is enough for the probe.
    if docker run --rm --gpus all debian:bookworm-slim nvidia-smi -L >/tmp/veyra-gpu 2>/dev/null; then
      GPU=1; ok "GPU: $(head -1 /tmp/veyra-gpu | cut -d'(' -f1)- the AI model runs on it"
    else
      GPU=0; info "no NVIDIA GPU visible to Docker; the AI model will run on the CPU (slower)"
    fi
  else
    GPU=0
  fi
  local drafting="off (rules / saved drafts)"
  [[ $LLM == 1 && $DRAFTER == ollama ]] && drafting="$MODEL (Ollama LLM)"
  [[ $LLM == 1 && $DRAFTER == laya ]] && drafting="$DECISION_MODEL (decision model)"
  info "Wazuh: $([[ $WAZUH == 1 ]] && echo on || echo "off (add --wazuh)")   AI drafter: $drafting"
}

# Which model drafts: the flag, else what the last run chose, else ask (a terminal, `up`, no
# --yes), else Ollama.
choose_drafter() {
  [[ -z $DECISION_MODEL ]] && DECISION_MODEL=$(env_value DECISION_MODEL "$STATE_FILE")
  [[ -z $DECISION_MODEL ]] && DECISION_MODEL=laya:en
  if [[ $LLM != 1 ]]; then DRAFTER=${DRAFTER:-ollama}; return; fi
  if [[ -z $DRAFTER ]]; then
    DRAFTER=$(env_value DRAFTER "$STATE_FILE")
    if [[ -n $DRAFTER ]]; then
      info "AI drafter: $DRAFTER, as chosen last time (change with --drafter ollama|laya)"
    elif [[ $CMD == up && $YES != 1 && -t 0 ]]; then
      printf '\n       %sWhich AI should draft contracts for new log formats?%s\n' "$B" "$N"
      printf '         %s1%s  Ollama LLM (%s)   most accurate in our tests; 1-2 s a draft; a few GB\n' "$B" "$N" "$MODEL"
      printf '         %s2%s  Laya decision model   about 0.1 s a draft; under 1 GB; leaves more fields unmapped\n' "$B" "$N"
      local answer; read -r -p "       Choose 1 or 2 [1]: " answer
      case "$answer" in 2|laya|Laya) DRAFTER=laya ;; *) DRAFTER=ollama ;; esac
    else
      DRAFTER=ollama
    fi
  fi
  case "$DRAFTER" in
    ollama|laya) ;;
    *) die "--drafter takes ollama or laya, not '$DRAFTER'" ;;
  esac
}

contracts() {
  stage "Log Contract registry"

  local parent_contracts="$REPO/../contracts-repo"

  # The contracts repo is expected one level above Veyra.
  if [[ -d "$parent_contracts" ]]; then
    CONTRACTS_REPO="$(cd "$parent_contracts" && pwd)"

    if [[ -d "$CONTRACTS_REPO/.git" ]]; then
      local commit
      commit=$(git -C "$CONTRACTS_REPO" rev-parse --short HEAD 2>/dev/null || echo "unknown")
      ok "found $CONTRACTS_REPO ($commit)"
    else
      warn "$CONTRACTS_REPO exists but is not a Git checkout"
      ok "using existing contracts repo: $CONTRACTS_REPO"
    fi
    return
  fi

  info "contracts repo not found at $parent_contracts"
  info "cloning $CONTRACTS_URL ..."

  git clone --quiet "$CONTRACTS_URL" "$parent_contracts" || \
    die "could not clone the contracts registry. Clone it yourself: git clone $CONTRACTS_URL ../contracts-repo"

  CONTRACTS_REPO="$(cd "$parent_contracts" && pwd)"
  ok "cloned to $CONTRACTS_REPO"
}



write_env() {
  local uid gid docker_gid console_port=8080
  uid=$(id -u); gid=$(id -g)
  [[ ${VEYRA_TOOLBOX:-0} == 1 || $uid == 0 ]] && { uid=1000; gid=1000; }
  # The demo engine restarts the stateful consumers over the Docker socket, so it needs the
  # socket's group. Anything else would have to run as root.
  docker_gid=$(stat -c %g /var/run/docker.sock 2>/dev/null || echo 999)
  if ! docker ps --format '{{.Names}}' | grep -qx veyra-caddy; then
    if (exec 3<>/dev/tcp/127.0.0.1/8080) 2>/dev/null; then console_port=8081; warn "port 8080 is busy; the console will be on 8081"; fi
  elif [[ -f .env.runtime ]]; then
    console_port=$(env_value VEYRA_CONSOLE_PORT .env.runtime); console_port=${console_port:-8080}
  fi
  mkdir -p data/state
  {
    echo "# generated by veyra.sh from profiles/$PROFILE.env (+ .env.local) - do not edit; re-run ./veyra.sh up"
    env_lines "profiles/$PROFILE.env"
    [[ -f .env.local ]] && env_lines .env.local
    echo "# --- veyra.sh overrides"
    echo "VEYRA_UID=$uid"
    echo "VEYRA_GID=$gid"
    echo "VEYRA_DOCKER_GID=$docker_gid"
    echo "VEYRA_CONSOLE_PORT=$console_port"
    # Only when the operator moved the registry: compose's default is ../../contracts-repo,
    # relative to compose/, which is the sibling checkout.
    if [[ -n ${VEYRA_CONTRACTS_REPO_DIR:-} ]]; then echo "VEYRA_CONTRACTS_REPO_DIR=$CONTRACTS_REPO"; fi
    echo "VEYRA_DEMO_MODE=1"
    echo "VEYRA_SEGMENT_MAX_SECONDS=20"
    echo "VEYRA_VAULT_CHATTR=0"
    [[ -n $MODEL ]] && echo "VEYRA_LLM_MODEL=$MODEL"
    if [[ $LLM != 1 ]]; then
      echo "VEYRA_LLM_MODE=cache"
    elif [[ $DRAFTER == laya ]]; then
      echo "VEYRA_LLM_BACKEND=decision"
      echo "VEYRA_DECISION_URL=http://ollaya:11435"
      echo "VEYRA_DECISION_MODEL=$DECISION_MODEL"
      # A decision model drafts in a second or two even on a CPU, so it drafts live on every
      # profile (the laptop profile keeps LLMs in cache mode because they need a GPU).
      echo "VEYRA_LLM_MODE=live_then_cache"
    else
      echo "VEYRA_OLLAMA_URL=http://ollama:11434"
    fi
    [[ ${1:-} == nollm ]] && echo "VEYRA_LLM_MODE=cache"
    [[ $WAZUH == 1 ]] || echo "VEYRA_WAZUH_MODE=remote"
    true
  } >.env.runtime
}

environment() {
  stage "Settings and data folders"
  write_env
  mkdir -p data/{kafka,clickhouse,immudb,caddy,vault,keys,state,control,sinks/wazuh,sinks/partner,vector/dmz,vector/core,llm_cache,wazuh}
  touch data/sinks/wazuh/veyra.ndjson data/sinks/partner/partner.ndjson
  # The toolbox runs as root, but the services run as uid 1000: let them write what it created.
  if [[ ${VEYRA_TOOLBOX:-0} == 1 || $(id -u) == 0 ]]; then chmod -R a+rwX data edge console "$CONTRACTS_REPO" 2>/dev/null || true; fi
  compose_files
  save_state
  ok ".env.runtime written (profile $PROFILE$([[ -f .env.local ]] && echo ' + .env.local'))"
}

build() {
  stage "Building images and the console (first run: several minutes)"
  info "Veyra Python image ..."
  dc --profile tools build tools
  docker image inspect veyra/python:0.1.0 >/dev/null 2>&1 || die "the Python image did not build; run: docker compose -f compose/docker-compose.yml --env-file .env.runtime --profile tools build tools"
  ok "veyra/python:0.1.0"
  local newest_src
  newest_src=$(find console/src console/package-lock.json console/index.html -type f -newer console/dist/index.html 2>/dev/null | head -1 || true)
  if [[ -f console/dist/index.html && -z $newest_src ]]; then
    ok "console already built (console/dist)"
  else
    info "console (npm ci + vite build in $NODE_IMAGE) ..."
    docker run --rm -v "$REPO/console:/app" -v veyra_console_node_modules:/app/node_modules -w /app "$NODE_IMAGE" \
      sh -c "npm ci --no-audit --no-fund --loglevel=error && npm run build --silent && chown -R $(id -u):$(id -g) dist" \
      2>&1 | shown 3
    [[ -f console/dist/index.html ]] || die "the console did not build"
    ok "console/dist"
  fi
  run_tools_nodeps python edge/render.py >/dev/null
  ok "edge collector configs rendered"
  run_tools_nodeps python -m veyra_evidence.keys init >/dev/null
  ok "evidence keys ready (data/keys)"
}

infrastructure() {
  stage "Starting Kafka, ClickHouse, immudb, the edges, Caddy and the Kafka UI"
  dc up -d --remove-orphans kafka clickhouse immudb edge-dmz edge-core caddy kafka-ui 2>&1 | shown 4
  wait_healthy veyra-kafka 240 || die "Kafka did not become healthy. See: $SELF logs kafka"
  ok "Kafka healthy"
  wait_healthy veyra-clickhouse 180 || die "ClickHouse did not become healthy. See: $SELF logs clickhouse"
  ok "ClickHouse healthy"
  local topics
  topics=$(run_tools python -m veyra_common.topics | awk '{c[$1]++} END {for (k in c) printf "%d %s, ", c[k], k}')
  ok "Kafka topics: ${topics%, }"
}

wazuh() {
  stage "Wazuh SIEM"
  if [[ $WAZUH != 1 ]]; then info "skipped (add --wazuh to include it)"; return; fi
  local current
  current=$(docker run --rm -q alpine:3.20 cat /proc/sys/vm/max_map_count 2>/dev/null || echo 0)
  if ((current < 262144)); then
    info "Wazuh's indexer needs vm.max_map_count >= 262144 in Docker's kernel (now $current)."
    if confirm "Set it now (a privileged one-off container; resets on reboot)?"; then
      # vm.* sysctls are not namespaced, so a privileged container sets it for Docker's kernel.
      docker run --rm -q --privileged alpine:3.20 sysctl -w vm.max_map_count=262144 >/dev/null
      ok "vm.max_map_count = 262144"
    else
      warn "left as is; the indexer may fail to start"
    fi
  fi
  if [[ ! -f data/wazuh/certs/root-ca.pem ]]; then
    info "generating certificates ..."
    mkdir -p data/wazuh/certs
    docker run --rm -v "$REPO/compose/wazuh/certs.yml:/config/certs.yml:ro" -v "$REPO/data/wazuh/certs:/certificates" \
      --entrypoint /bin/bash wazuh/wazuh-certs-generator:0.0.2 -c /entrypoint.sh >/dev/null
    ok "certificates in data/wazuh/certs"
  fi
  dc up -d wazuh-indexer wazuh-manager wazuh-dashboard 2>&1 | shown 3
  local left=300
  until docker exec veyra-wazuh-indexer curl -sk -o /dev/null https://localhost:9200 2>/dev/null; do
    ((left <= 0)) && die "the Wazuh indexer did not answer. See: $SELF logs wazuh-indexer"
    printf '       %swaiting for the Wazuh indexer %3ss%s\r' "$D" "$left" "$N"; sleep 5; left=$((left - 5))
  done
  printf '\n'
  if [[ ! -f data/state/wazuh_init.done ]]; then
    info "initialising the indexer's security config (once) ..."
    docker exec -e JAVA_HOME=/usr/share/wazuh-indexer/jdk veyra-wazuh-indexer bash -c '
      C=/usr/share/wazuh-indexer/config/certs
      bash /usr/share/wazuh-indexer/plugins/opensearch-security/tools/securityadmin.sh \
        -cd /usr/share/wazuh-indexer/config/opensearch-security/ -nhnv \
        -cacert $C/root-ca.pem -cert $C/admin.pem -key $C/admin-key.pem -p 9200 -icl' 2>&1 | shown 1
    touch data/state/wazuh_init.done
  fi
  ok "Wazuh up: dashboard https://localhost:8443 (admin / admin)"
}

llm() {
  stage "AI drafter model"
  if [[ $LLM != 1 ]]; then info "skipped: drafts use saved answers and the rules drafter (cache mode)"; return; fi
  if [[ $DRAFTER == laya ]]; then decision_model; return; fi
  dc up -d ollama 2>&1 | shown 2
  wait_healthy veyra-ollama 120 || die "the Ollama container did not start. See: $SELF logs ollama"
  if docker exec veyra-ollama ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$MODEL"; then
    ok "$MODEL already downloaded"
  else
    info "downloading $MODEL (one time; a few GB) ..."
    if docker exec veyra-ollama ollama pull "$MODEL"; then ok "$MODEL ready"; else
      warn "could not download $MODEL; drafts will use saved answers / rules (cache mode)"
      write_env nollm
    fi
  fi
  docker exec veyra-ollama ollama run "$MODEL" "ok" >/dev/null 2>&1 && ok "model loaded $([[ $GPU == 1 ]] && echo 'on the GPU' || echo 'on the CPU')" || warn "the model did not warm up; the first draft may be slow"
}

# The decision-model server (Ollaya) and its model, in place of Ollama.
decision_model() {
  info "decision model server (Ollaya) ..."
  dc up -d ollaya 2>&1 | shown 2
  wait_healthy veyra-ollaya 120 || die "the Ollaya container did not start. See: $SELF logs ollaya"
  if docker exec veyra-ollaya ollaya list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$DECISION_MODEL"; then
    ok "$DECISION_MODEL already downloaded"
  else
    info "downloading $DECISION_MODEL (one time; under 1 GB for laya:en) ..."
    if docker exec veyra-ollaya ollaya pull "$DECISION_MODEL" 2>&1 | shown 1; then ok "$DECISION_MODEL ready"; else
      warn "could not download $DECISION_MODEL; drafts will use saved answers / rules (cache mode)"
      write_env nollm
      return
    fi
  fi
  # Loads the model through the same client control-api uses, so a wrong URL shows up here.
  run_tools python -c "from veyra_common.settings import settings; from veyra_contracts.drafting.backends import make_client; make_client(settings).warm()" >/dev/null 2>&1 \
    && ok "model loaded $([[ $GPU == 1 ]] && echo 'on the GPU' || echo 'on the CPU')" || warn "the model did not warm up; the first draft may be slow"
}

services() {
  stage "Starting the Veyra services"
  # Extra normalizers left running by a load test are not part of `up`, so they would keep the
  # old image. One consumer group cannot run two versions: stop them, and start them again below.
  local extra=() svc
  while read -r svc; do
    [[ $svc =~ ^normalizer-[0-9]+$ ]] && extra+=("$svc")
  done < <(dc --profile scale ps --services --status running 2>/dev/null || true)
  if ((${#extra[@]})); then dc --profile scale stop "${extra[@]}" >/dev/null 2>&1 || true; fi
  dc up -d --remove-orphans 2>&1 | shown 12
  if ((${#extra[@]})); then
    dc --profile scale up -d "${extra[@]}" 2>&1 | shown 2
    ok "extra normalizers restarted on this build: ${#extra[@]}"
  fi
  info "waiting for every service to answer ..."
  run_tools python tools/veyra_check.py --wait 240 --only-health >/dev/null 2>&1 \
    && ok "all services answering" || warn "some services are slow to start; the smoke check below shows which"
}

smoke() {
  stage "Smoke check"
  if run_tools python tools/veyra_check.py; then
    SMOKE_OK=1
  else
    SMOKE_OK=0
    warn "some checks failed; see above. $SELF logs <service> shows why."
  fi
}

summary() {
  stage "Ready"
  local port; port=$(env_value VEYRA_CONSOLE_PORT .env.runtime); port=${port:-8080}
  cat <<EOF
       ${B}Console${N}          http://localhost:$port          sign in: admin@veyra / author@maha / approver@veyra  (password: veyra-demo)
       ${B}Kafka UI${N}         http://localhost:8085          topics, partitions, consumer lag, live rates
       ${B}Control API${N}      http://localhost:8000/docs     OpenAPI for sources, keys, contracts, drift, replay
       ${B}Evidence API${N}     http://localhost:8100/docs     verify, proof-pack export, signed roots
       ${B}Push endpoint${N}    http://localhost:8088          Splunk-HEC compatible, per-source API keys
       ${B}Syslog in${N}        udp/tcp 5514/5515 (DMZ), 5524/5525 (core)
EOF
  [[ $WAZUH == 1 ]] && echo "       ${B}Wazuh${N}            https://localhost:8443         admin / admin"
  if [[ $LLM == 1 && $DRAFTER == laya ]]; then
    echo "       ${B}AI drafter${N}       $DECISION_MODEL, a decision model (switch: $SELF up --drafter ollama)"
  elif [[ $LLM == 1 ]]; then
    echo "       ${B}AI drafter${N}       $MODEL, an Ollama LLM (switch: $SELF up --drafter laya)"
  fi
  cat <<EOF

       Next:  ${B}$SELF demo${N}     guided walkthrough of every feature (≈5 min)
              ${B}$SELF load${N}     Kafka + pipeline throughput test
              ${B}$SELF status${N}   health at a glance        ${B}$SELF down${N}   stop everything
EOF
}

# ---------------------------------------------------------------- commands
cmd_up() {
  local started=$SECONDS
  printf '%sVeyra%s  one-command setup   %s(%s)%s\n' "$B" "$N" "$D" "$REPO" "$N"
  preflight; contracts; environment; build; infrastructure; wazuh; llm; services; smoke; summary
  printf '\n       %sdone in %d min %d s%s\n' "$D" $(((SECONDS - started) / 60)) $(((SECONDS - started) % 60)) "$N"
  # The stack is only "up" when the smoke check says so: a silent pass here is how a broken
  # stack gets taken into a rehearsal.
  if [[ ${SMOKE_OK:-0} != 1 ]]; then
    printf '\n       %sFAILED%s  the smoke check did not pass; this stack is not demo-ready.\n' "$R" "$N"
    printf '       %sRun %s status%s for a per-service view, %s logs <service>%s for the reason.\n' "$D" "$SELF" "$N" "$SELF" "$N"
    return 1
  fi
}

need_up() {
  load_state
  [[ -f .env.runtime && -n ${PROFILE:-} ]] || die "the stack has not been set up here yet. Run: $SELF up"
  compose_files
}

cmd_clean() {
  export VEYRA_NO_DEMO=1
  # Override default profiles to exclude 'b7' (demo-engine)
  SERVICE_PROFILES=(a2 a3 a6 c1 c3 b1 b2 b3 b4)
  cmd_up
}

cmd_demo() {
  need_up; STAGES=1; stage "Guided demo"
  # The walkthrough prints its own PASS/FAIL tally; just hand its exit code back.
  run_tools_tty python tools/demo/walkthrough.py "${PASSTHRU[@]}" || exit $?
}
cmd_status() { need_up; dc ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'; echo; run_tools python tools/veyra_check.py --no-flow || true; }
cmd_logs() { need_up; dc logs -f --tail 200 "${PASSTHRU[@]}"; }
cmd_down() { need_up; dc --profile scale --profile tools down --remove-orphans; ok "stopped (data kept; $SELF up starts it again)"; }
cmd_reset() {
  need_up
  # The demo engine's reset is the whole world: control plane, Kafka, ClickHouse, the vault,
  # the sinks and the drift state, inside VEYRA_DEMO_RESET_BUDGET_S.
  run_tools python -m demo_engine.cli --in-container reset || exit $?
  ok "reset to the seeded demo world (control plane, Kafka, ClickHouse, vault, sinks, drift)"
}
cmd_wipe() {
  load_state; compose_files 2>/dev/null || true
  confirm "Stop Veyra and DELETE all its data (Kafka, ClickHouse, vault, keys, Wazuh)?" || { info "cancelled"; return; }
  [[ -f .env.runtime ]] && dc --profile scale --profile tools down -v --remove-orphans || true
  docker run --rm -q -v "$REPO:/repo" alpine:3.20 sh -c 'rm -rf /repo/data /repo/.env.runtime'
  ok "wiped. $SELF up starts fresh"
}
cmd_doctor() {
  preflight
  for p in 8080 8085 8088 8000 8100 9092 29092 8123 5433 8443 9200 11434; do
    if (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then info "port $p: in use"; else info "port $p: free"; fi
  done
}

cmd_load() {
  need_up
  STAGES=2
  local partitions free need
  partitions=$(env_value VEYRA_RAW_PARTITIONS_PER_VENDOR .env.runtime); partitions=$((${partitions:-3} * 2))
  free=$(kafka_free_bytes)
  if [[ $LOAD_SKIP_KAFKA == 0 ]]; then
    stage "Stage A: Kafka alone, $(printf "%'d" "$LOAD_EVENTS") records of $LOAD_SIZE bytes"
    need=$((LOAD_EVENTS * LOAD_SIZE))
    info "worst case on disk: $(human $need) (before zstd); free: $(human "$free"); topic kept for 1 hour, size-capped"
    if ((need > free * 7 / 10)) && [[ $LOAD_FORCE == 0 ]]; then
      die "that would use more than 70% of the free disk. Lower --events or pass --force."
    fi
    local cap=$(( free * 6 / 10 / partitions ))
    docker exec veyra-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --delete --topic bench.load >/dev/null 2>&1 || true
    sleep 2
    docker exec veyra-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --create --topic bench.load \
      --partitions "$partitions" --config retention.ms=3600000 --config retention.bytes="$cap" --config compression.type=producer >/dev/null
    ok "topic bench.load: $partitions partitions"
    info "producing (acks=all, idempotent, zstd) - watch it live in the Kafka UI at http://localhost:8085 ..."
    docker run --rm --network veyra_net -e KAFKA_HEAP_OPTS="-Xmx2g" "$KAFKA_IMAGE" /opt/kafka/bin/kafka-producer-perf-test.sh \
      --topic bench.load --num-records "$LOAD_EVENTS" --record-size "$LOAD_SIZE" --throughput -1 \
      --producer-props bootstrap.servers=kafka:9092 acks=all enable.idempotence=true compression.type=zstd linger.ms=20 batch.size=1048576 \
      | sed 's/^/       /'
    info "consuming it back ..."
    docker run --rm --network veyra_net -e KAFKA_HEAP_OPTS="-Xmx2g" "$KAFKA_IMAGE" /opt/kafka/bin/kafka-consumer-perf-test.sh \
      --bootstrap-server kafka:9092 --topic bench.load --messages "$LOAD_EVENTS" --timeout 120000 2>/dev/null | sed 's/^/       /' || true
    docker exec veyra-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --delete --topic bench.load >/dev/null 2>&1 || true
    ok "bench.load deleted"
  fi
  if [[ $LOAD_SKIP_PIPELINE == 0 ]]; then
    stage "Stage B: the real pipeline, $(printf "%'d" "$LOAD_PIPELINE") envelopes through the normalizers"
    need=$((LOAD_PIPELINE * 4096))
    if ((need > free * 7 / 10)) && [[ $LOAD_FORCE == 0 ]]; then
      die "about $(human $need) of topics, vault and index would be written; lower --pipeline or pass --force."
    fi
    local replicas; replicas=$(env_value VEYRA_NORMALIZER_REPLICAS .env.runtime); replicas=${replicas:-1}
    if ((replicas > 1)); then
      local extra=(); for i in $(seq 2 "$((replicas > 6 ? 6 : replicas))"); do extra+=("normalizer-$i"); done
      dc --profile scale up -d "${extra[@]}" 2>&1 | shown 2
      ok "normalizers: $((${#extra[@]} + 1)) instances"
    fi
    run_tools python tools/bench/load_raw.py --events "$LOAD_PIPELINE" "${PASSTHRU[@]}"
  fi
}

case "$CMD" in
  up) cmd_up ;;
  clean) cmd_clean ;;
  demo) cmd_demo ;;
  load) cmd_load ;;
  status) cmd_status ;;
  logs) cmd_logs ;;
  down|stop) cmd_down ;;
  reset) cmd_reset ;;
  wipe) cmd_wipe ;;
  doctor) cmd_doctor ;;
  help|-h|--help) usage ;;
  *) usage; die "unknown command: $CMD" ;;
esac
