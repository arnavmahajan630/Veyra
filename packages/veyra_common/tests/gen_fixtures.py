"""Regenerate packages/veyra_common/fixtures/*.json.

Fixtures are the contract-test surface (01_TEAM_GUIDE §7): every consumer of a record
type tests against the producer's fixture, so changing one fails the other's CI on
purpose. Run ``python packages/veyra_common/tests/gen_fixtures.py`` after an
intentional model change, then commit the diff.

The envelope fixtures double as the A1/A2 parity vectors: the same raw bytes stamped by
Vector's VRL must produce the same ``raw_sha256`` and ``raw_b64``.
"""

from __future__ import annotations

import json
from pathlib import Path

from veyra_common.envelope import stamp
from veyra_common.hashing import sha256_hex, template_sig
from veyra_common.models import (
    ApiKeyMessage,
    AuditRecord,
    ClassHint,
    ContractMessage,
    ContractRef,
    DlqRecord,
    EncodingInfo,
    EnrichMessage,
    LineageRecord,
    NormEvent,
    RawRef,
    Receipt,
    RoutesMessage,
    RouteSpec,
    ShadowRecord,
    SignedRoot,
    SourceMessage,
    TemplateRef,
    TimeInfo,
    Ulpf,
    VaultIndexEvent,
    VaultIndexSegment,
    VocabMessage,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# The three demo lines, verbatim from docs/plan/reference/spec_vectors.py.
RAW_T3 = (
    b'<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login '
    b'from 103.21.4.77 via 10.2.3.4 attempts:1"} | trace=\n  at com.x.Auth.login(Auth.java:88)'
)
RAW_SSHD = (
    b"<86>Sep 26 14:05:12 core-lnx-07 sshd[4410]: Failed password for invalid user admin "
    b"from 45.12.3.9 port 52144 ssh2"
)
RAW_CEF = b"CEF:0|Acme|NGFW|9.1|100|traffic deny|5|src=45.12.3.9 dst=10.2.3.4 dpt=22 act=deny"

UID_T3 = "0192a4f0-0000-7000-8000-000000000001"
UID_SSHD = "0192a4f0-0000-7000-8000-000000000002"
UID_CEF = "0192a4f0-0000-7000-8000-000000000003"
RECEIVED = "2026-09-26T08:35:11.123456789Z"
TEXT_T3 = "user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1"
SHA_T3 = sha256_hex(RAW_T3)


def envelopes() -> dict[str, object]:
    """The parity vectors: one per transport/framing shape."""
    t3 = stamp(
        RAW_T3,
        collector_id="edge-dmz-01",
        transport="syslog_tcp",
        framing_method="multiline_join",
        parts=2,
        zone="dmz",
        tenant_id="t_maha_power",
        source_id="src_authsrv_01",
        vendor="custom",
        listener="dmz-tcp",
        peer_ip="172.20.0.21",
        peer_port=40112,
        auth_method="ip_map",
        event_uid=UID_T3,
        received_time=RECEIVED,
    )
    sshd = stamp(
        RAW_SSHD,
        collector_id="edge-core-01",
        transport="syslog_udp",
        framing_method="datagram",
        zone="core",
        tenant_id="t_ntro_core",
        source_id="src_lnx_core_07",
        vendor="linux",
        listener="core-udp",
        peer_ip="172.20.0.31",
        peer_port=51422,
        auth_method="ip_map",
        event_uid=UID_SSHD,
        received_time=RECEIVED,
    )
    cef = stamp(
        RAW_CEF,
        collector_id="ingest-gateway-01",
        transport="http_hec_event",
        framing_method="http_body",
        zone="dmz",
        tenant_id="t_ntro_core",
        source_id="src_fw_dmz_01",
        vendor="acme_ngfw",
        auth_method="api_key",
        auth_key_id="k_7QX2MPLA",
        event_uid=UID_CEF,
        received_time=RECEIVED,
    )
    return {
        "note": "A1/A2 parity vectors — Vector VRL and veyra_common.envelope.stamp must agree",
        "vectors": [
            {"name": "t3_multiline_syslog_tcp", "envelope": t3.model_dump(mode="json")},
            {"name": "sshd_datagram_udp", "envelope": sshd.model_dump(mode="json")},
            {"name": "cef_http_hec_event", "envelope": cef.model_dump(mode="json")},
        ],
    }


def ulpf_tier3() -> Ulpf:
    return Ulpf(
        event_uid=UID_T3,
        tenant_id="t_maha_power",
        source_id="src_authsrv_01",
        vendor="custom",
        zone="dmz",
        raw_ref=RawRef(topic="raw.custom", partition=1, offset=4412),
        raw_sha256=SHA_T3,
        received_time=RECEIVED,
        custody="realtime",
        contract=ContractRef(id="authsrv", version=1),
        template=TemplateRef(sig=template_sig("authsrv", TEXT_T3), id=None),
        tier=3,
        conformance="unknown_template",
        parse_path=["syslog:rfc3164", "json", "auto:kv(rest)"],
        field_offsets={},
        derived_fields={"severity_id": "vocab:severity_words", "time": "ts:inferred_year"},
        class_hint=ClassHint(class_uid=3002, confidence="medium"),
        time=TimeInfo(
            source="event", tz_assumed="Asia/Kolkata", year_inferred=True, clock_skew_ms=-812
        ),
        encoding=EncodingInfo(detected="utf-8", confidence=0.99, invalid_bytes=0),
        pii_fields=["user"],
        engine_version="0.1.0",
    )


def norm_event() -> NormEvent:
    return NormEvent(
        class_uid=0,
        category_uid=0,
        type_uid=99,
        activity_id=99,
        severity_id=3,
        time=1790400311000,
        message=TEXT_T3,
        raw_data=RAW_T3.decode(),
        observables=[
            {"name": "ip_1", "type_id": 2, "value": "103.21.4.77"},
            {"name": "ip_2", "type_id": 2, "value": "10.2.3.4"},
            {"name": "user", "type_id": 4, "value": "a.sharma"},
        ],
        unmapped={
            "user": "a.sharma",
            "attempts": "1",
            "syslog.host": "fw01",
            "syslog.app": "app",
            "trace": "at com.x.Auth.login(Auth.java:88)",
        },
        metadata={"version": "1.9.0", "product": {"name": "VEYRA", "vendor_name": "NTRO"}},
        ulpf=ulpf_tier3(),
    )


def records() -> dict[str, object]:
    sig = template_sig("authsrv", TEXT_T3)
    return {
        "lineage.json": LineageRecord(
            event_uid=UID_T3,
            revision=1,
            tenant_id="t_maha_power",
            source_id="src_authsrv_01",
            raw_ref=RawRef(topic="raw.custom", partition=1, offset=4412),
            raw_sha256=SHA_T3,
            contract_ref="authsrv@1",
            template_sig=sig,
            template_id=None,
            tier=3,
            conformance="unknown_template",
            class_uid=0,
            category="uncategorized",
            norm_topic="norm.uncategorized",
            produced_at="2026-09-26T08:35:11.200000000Z",
            search_terms=["103.21.4.77", "10.2.3.4", "a.sharma", "fw01"],
        ),
        "dlq.json": DlqRecord(
            event_uid=UID_T3,
            tenant_id="t_maha_power",
            source_id="src_authsrv_01",
            tier=3,
            reason_code="no_template_match",
            reason_detail="authsrv@1 has 2 templates, none matched",
            contract_ref="authsrv@1",
            template_sig=sig,
            text_masked="user=<USER_1> FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1",
            parse_path=["syslog:rfc3164", "json", "auto:kv(rest)"],
            produced_at="2026-09-26T08:35:11.200000000Z",
        ),
        "shadow.json": ShadowRecord(
            event_uid=UID_T3,
            contract_id="authsrv",
            active_ref="authsrv@1",
            candidate_ref="authsrv@2",
            active_tier=3,
            candidate_tier=1,
            changed_fields=["user.name", "src_endpoint.ip", "dst_endpoint.ip", "status_id"],
            regressions=[],
            produced_at="2026-09-26T08:35:11.210000000Z",
        ),
        "vault_index_event.json": VaultIndexEvent(
            event_uid=UID_T3,
            raw_topic="raw.custom",
            partition=1,
            offset=4412,
            segment_id="seg_raw.custom_1_000000004400",
            record_idx=12,
            chain_hash_hex="aa0fe44eda2a9379e4e60997c65579b0a747adfd0f835199190df147cb7d2c2d",
            sealed_at="2026-09-26T08:35:30.000000000Z",
        ),
        "vault_index_segment.json": VaultIndexSegment(
            segment_id="seg_raw.custom_1_000000004400",
            raw_topic="raw.custom",
            partition=1,
            first_offset=4400,
            last_offset=4431,
            record_count=32,
            prev_chain_hash_hex="a93423deac995ba0e328e4dadbe29d43c77d61f273f1f0d0410b65ad6cf41f53",
            last_chain_hash_hex="dea0f94c4673dca513810832005ceeb966e26e26a30a638d61e2bbcfa0da6b97",
            segment_digest_hex="d361f24129e5f0063c8420b399f7295b0bc7e9e61e2f8a9e4c9310d9c33b8949",
            sealed_at="2026-09-26T08:35:30.000000000Z",
        ),
        "receipt.json": Receipt(
            event_uid=UID_T3,
            revision=1,
            route_id="wazuh_main",
            status="delivered",
            detail="",
            at="2026-09-26T08:35:11.400000000Z",
        ),
        "audit.json": AuditRecord(
            actor="approver@veyra",
            role="pack_approver",
            action="contract.promote",
            target="authsrv@2",
            detail="canary -> active, author=author@maha",
            at="2026-09-26T08:36:02.000000000Z",
        ),
        "signed_root.json": SignedRoot(
            window_id="w_1790000000",
            window_start=1790000000,
            window_end=1790000060,
            leaf_count=3,
            root="49a27ff0ee8487600104ea234555fdaa1b3f8ac98d67d12fa3c3620a0e7000f4",
            segments=[
                "seg_raw.custom_1_000000004400",
                "seg_raw.linux_0_000000001200",
                "seg_raw.acme_ngfw_2_000000000300",
            ],
            prev_signed_sha256="a93423deac995ba0e328e4dadbe29d43c77d61f273f1f0d0410b65ad6cf41f53",
            key_id="veyra-root-ed25519-1",
        ),
        "control_apikey.json": ApiKeyMessage(
            key_id="k_7QX2MPLA",
            secret_sha256="9f2c1f5b4f0c2d7a1b3e4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091",
            pepper_id="p1",
            source_id="src_authsrv_01",
            tenant_id="t_maha_power",
            status="active",
            quota_eps=500,
            created_at="2026-09-26T08:30:00.000000000Z",
        ),
        "control_source.json": SourceMessage(
            source_id="src_authsrv_01",
            tenant_id="t_maha_power",
            vendor="custom",
            zone="dmz",
            transport="http_hec_event",
            contract_id="authsrv",
            expected_eps=5.0,
            salt_buckets=1,
            status="active",
        ),
        "control_contract.json": ContractMessage(
            id="authsrv",
            version=1,
            state="active",
            tenant_id="t_maha_power",
            sources=["src_authsrv_01"],
            compiled={
                "contract": "authsrv",
                "version": 1,
                "tenant": "t_maha_power",
                "sources": ["src_authsrv_01"],
                "envelope": [{"syslog": {"variant": "auto"}}, {"json": {"text_field": "msg"}}],
                "time": {"field": "syslog.timestamp", "formats": ["%b %d %H:%M:%S"]},
                "templates": [],
                "required": ["time"],
                "compiler_version": "0.0.0-s0-stub",
            },
            candidate=None,
            published_at="2026-09-26T08:30:05.000000000Z",
        ),
        "control_enrich.json": EnrichMessage(
            name="zone_map",
            version=1,
            rows=[
                {"cidr": "10.2.0.0/16", "zone": "core", "site": "maha-pune-1"},
                {"cidr": "172.20.0.0/16", "zone": "dmz", "site": "maha-pune-1"},
            ],
        ),
        "control_vocab.json": VocabMessage(
            name="status_words",
            version=1,
            entries={"FAILED": {"status_id": 2}, "OK": {"status_id": 1}},
        ),
        "control_routes.json": RoutesMessage(
            routes=[
                RouteSpec(
                    id="wazuh_main",
                    filter={"tenants": ["*"], "tiers": [1, 2, 3, 4]},
                    format="ocsf_json",
                    masking="none",
                    sink={
                        "type": "ndjson_file",
                        "path": "/sinks/wazuh/veyra.ndjson",
                        "fsync_ms": 200,
                    },
                ),
                RouteSpec(
                    id="partner_masked",
                    filter={"tenants": ["t_maha_power"], "classes": [3002, 4001]},
                    format="ocsf_json",
                    masking={
                        "user.name": "hmac",
                        "src_endpoint.ip": "hmac",
                        "raw_data": "redact",
                    },
                    sink={"type": "ndjson_file", "path": "/sinks/partner/partner.ndjson"},
                ),
            ]
        ),
    }


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    (FIXTURES / "envelope_vectors.json").write_text(
        json.dumps(envelopes(), indent=2, sort_keys=True) + "\n"
    )
    written.append("envelope_vectors.json")

    (FIXTURES / "envelope.json").write_text(
        json.dumps(
            envelopes()["vectors"][0]["envelope"],  # type: ignore[index]
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    written.append("envelope.json")

    (FIXTURES / "ulpf.json").write_text(
        json.dumps(ulpf_tier3().model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )
    (FIXTURES / "norm_event.json").write_text(
        json.dumps(norm_event().model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )
    written += ["ulpf.json", "norm_event.json"]

    for name, model in records().items():
        (FIXTURES / name).write_text(
            json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
        written.append(name)

    for name in sorted(written):
        print(f"wrote fixtures/{name}")


if __name__ == "__main__":
    main()
