"""Publish `control` messages the way C1 will, so A's services can run before C1 exists.

**A-owned scaffolding, not production code.** C1 owns the real publisher; when it lands, delete
this file. It exists because the normalizer, the gateway and the router all rebuild their state
from the compacted `control` topic (IF-CONTROL), and without a publisher they would sit at tier 4
forever.

    uv run python tools/mock_control_publish.py                    # seeded contracts + sources
    uv run python tools/mock_control_publish.py --candidate authsrv_v2
    uv run python tools/mock_control_publish.py --list

It compiles contracts with `veyra_engine.testing.mini_compile` (A3's stand-in for C2's compiler),
so what the engine loads here is the same shape C2 will publish.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from veyra_common.hashing import sha256_hex
from veyra_common.ids import new_api_key_id, new_api_key_secret
from veyra_common.kafka import make_producer
from veyra_common.models import (
    ApiKeyMessage,
    ContractMessage,
    RoutesMessage,
    RouteSpec,
    SourceMessage,
    VocabMessage,
    control_key,
)
from veyra_common.settings import Settings
from veyra_common.topics import TOPIC_CONTROL
from veyra_engine import mini_compile

REPO = Path(__file__).resolve().parents[1]
SEED_CONTRACTS = REPO / "contracts-repo" / "t_ntro_core"
TEST_CONTRACTS = REPO / "packages" / "veyra_engine" / "tests" / "contracts"

# The pre-seeded world from 04_DEMO_SCRIPT.md §2, so a local run matches the demo's starting state.
SOURCES: list[dict[str, Any]] = [
    {
        "source_id": "src_fw_dmz_01",
        "tenant_id": "t_ntro_core",
        "vendor": "acme_ngfw",
        "zone": "dmz",
        "transport": "syslog_tcp",
        "contract_id": "acme_ngfw_cef",
        "expected_eps": 5.0,
    },
    {
        "source_id": "src_lnx_core_07",
        "tenant_id": "t_ntro_core",
        "vendor": "linux",
        "zone": "core",
        "transport": "syslog_udp",
        "contract_id": "linux_sshd",
        "expected_eps": 8.0,
    },
    {
        "source_id": "src_authsrv_01",
        "tenant_id": "t_maha_power",
        "vendor": "custom",
        "zone": "dmz",
        "transport": "http_hec_event",
        "contract_id": "authsrv",
        "expected_eps": 3.0,
    },
]

# Status words, the vocabulary the seeded contracts reference.
VOCAB = {
    "status_words": {
        "OK": {"status_id": 1},
        "SUCCESS": {"status_id": 1},
        "ACCEPTED": {"status_id": 1},
        "FAILED": {"status_id": 2},
        "FAILURE": {"status_id": 2},
        "DENIED": {"status_id": 2},
        "INVALID": {"status_id": 2},
    }
}

ROUTES = RoutesMessage(
    routes=[
        RouteSpec(
            id="wazuh_main",
            filter={"tenants": ["*"], "tiers": [1, 2, 3, 4]},
            format="ocsf_json",
            masking="none",
            sink={"type": "ndjson_file", "path": "/sinks/wazuh/veyra.ndjson", "fsync_ms": 200},
        )
    ]
)


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")


def contract_files(include_authsrv: bool) -> list[Path]:
    files = sorted(SEED_CONTRACTS.glob("*.yaml"))
    if include_authsrv:
        # In the real demo authsrv is created live in Beat 2; publishing it here is what lets A
        # test the full tier-1 path for the Maha Power source before C1 exists.
        files.append(TEST_CONTRACTS / "authsrv.yaml")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-authsrv", action="store_true", help="omit the live-onboarded contract"
    )
    parser.add_argument(
        "--candidate",
        help="also publish this contract file (in tests/contracts) as the canary candidate",
    )
    parser.add_argument("--api-key", action="store_true", help="also issue an API key for authsrv")
    parser.add_argument("--list", action="store_true", help="print what would be published")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = Settings()
    messages: list[tuple[str, dict[str, Any] | None]] = []

    compiled_by_id: dict[str, dict[str, Any]] = {}
    for path in contract_files(include_authsrv=not args.no_authsrv):
        compiled = mini_compile(path.read_text())
        compiled_by_id[compiled["contract"]] = compiled

    candidate_for: dict[str, dict[str, Any]] = {}
    if args.candidate:
        candidate_path = TEST_CONTRACTS / f"{args.candidate}.yaml"
        if not candidate_path.exists():
            raise SystemExit(f"no such contract file: {candidate_path}")
        candidate = mini_compile(candidate_path.read_text())
        candidate_for[candidate["contract"]] = candidate

    for contract_id, compiled in sorted(compiled_by_id.items()):
        candidate = candidate_for.get(contract_id)
        message = ContractMessage(
            id=contract_id,
            version=int(compiled["version"]),
            state="active",
            tenant_id=str(compiled.get("tenant") or "t_ntro_core"),
            sources=list(compiled.get("sources") or []),
            compiled=compiled,
            candidate=(
                {"version": int(candidate["version"]), "compiled": candidate} if candidate else None
            ),
            published_at=now(),
        )
        messages.append((control_key("contract", contract_id), message.model_dump(mode="json")))

    for source in SOURCES:
        message = SourceMessage(**source, salt_buckets=1, status="active")
        messages.append(
            (control_key("source", source["source_id"]), message.model_dump(mode="json"))
        )

    for name, entries in VOCAB.items():
        message = VocabMessage(name=name, version=1, entries=entries)
        messages.append((control_key("vocab", name), message.model_dump(mode="json")))

    messages.append(("routes", ROUTES.model_dump(mode="json")))

    if args.api_key:
        # The gateway (A2) compares sha256(pepper || secret); the pepper file is C1's to create,
        # so create it here if it is missing and print the secret once, like the real flow does.
        pepper_path = cfg.keys_dir / "api_pepper"
        pepper_path.parent.mkdir(parents=True, exist_ok=True)
        if not pepper_path.exists():
            import secrets

            pepper_path.write_text(secrets.token_hex(32))
            pepper_path.chmod(0o600)
        pepper = pepper_path.read_text().strip()
        key_id = new_api_key_id()
        secret = new_api_key_secret()
        key = ApiKeyMessage(
            key_id=key_id,
            secret_sha256=sha256_hex((pepper + secret).encode()),
            pepper_id="p1",
            source_id="src_authsrv_01",
            tenant_id="t_maha_power",
            status="active",
            quota_eps=cfg.gateway_default_quota_eps,
            created_at=now(),
        )
        messages.append((control_key("apikey", key_id), key.model_dump(mode="json")))
        print(f"issued key {key_id} with secret {secret} (shown once, like the real flow)")

    if args.list or args.dry_run:
        for key, value in messages:
            summary = "tombstone" if value is None else f"{len(json.dumps(value))} bytes"
            print(f"{key:<40} {summary}")
        return 0

    producer = make_producer(cfg=cfg)
    for key, value in messages:
        producer.produce(
            TOPIC_CONTROL,
            key=key,
            value=None if value is None else json.dumps(value).encode(),
        )
    producer.flush(30)
    print(f"published {len(messages)} control messages to {TOPIC_CONTROL}")
    for key, _ in messages:
        print(f"  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
