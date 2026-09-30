"""Filters, formats and masking — the three decisions that make a route either correct or a leak.

The one that matters most is the filter: `partner_masked` sends **one tenant's** events to an
outside party. A filter that is too generous does not fail loudly — it quietly ships NTRO's logs to
Maha Power's partner, which is the kind of bug this file exists to make impossible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from router.filters import Filter
from router.formats import SRC_IP_FIELD, ocsf_json, render
from router.keys import ensure_route_key, route_key
from router.masking import REDACTED, MaskingPlan, hmac_value
from router.routes import compile_route, compile_routes, load_routes_file
from router.settings import RouterSettings
from router_helpers import norm_event

REPO = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------- filters
def test_an_empty_filter_accepts_everything() -> None:
    accept = Filter.parse({})
    for tier in (1, 2, 3, 4):
        assert accept.accepts(norm_event(tier=tier))


def test_the_wazuh_filter_takes_every_tier_from_every_tenant() -> None:
    accept = Filter.parse({"tenants": ["*"], "tiers": [1, 2, 3, 4]})
    assert accept.accepts(norm_event(tier=1, tenant="t_ntro_core"))
    assert accept.accepts(norm_event(tier=4, tenant="t_maha_power"))


def test_the_partner_filter_excludes_other_tenants() -> None:
    """AC3's real content: NTRO events must be absent from the partner feed."""
    accept = Filter.parse({"tenants": ["t_maha_power"], "classes": [3002, 4001]})
    assert accept.accepts(norm_event(tenant="t_maha_power", class_uid=3002))
    assert not accept.accepts(norm_event(tenant="t_ntro_core", class_uid=3002))


def test_the_partner_filter_excludes_other_classes() -> None:
    accept = Filter.parse({"tenants": ["t_maha_power"], "classes": [3002, 4001]})
    assert not accept.accepts(norm_event(class_uid=1007))
    # A tier-3 event has class_uid 0 by design, so a class filter excludes it — worth knowing,
    # because it means the partner feed sees only what VEYRA could classify.
    assert not accept.accepts(norm_event(tier=3))


def test_tier_and_source_filters() -> None:
    assert Filter.parse({"tiers": [3, 4]}).accepts(norm_event(tier=3))
    assert not Filter.parse({"tiers": [3, 4]}).accepts(norm_event(tier=1))
    assert Filter.parse({"sources": ["src_authsrv_01"]}).accepts(norm_event())
    assert not Filter.parse({"sources": ["src_other"]}).accepts(norm_event())
    assert Filter.parse({"sources": ["*"]}).accepts(norm_event(source="anything"))


def test_a_misspelled_filter_key_is_refused_at_load() -> None:
    """A typo that silently matched everything would be a tenant leak."""
    with pytest.raises(ValueError, match="unknown filter key"):
        Filter.parse({"tenant": ["t_maha_power"]})


def test_a_filter_value_of_the_wrong_type_is_refused() -> None:
    with pytest.raises(ValueError, match="expected a list"):
        Filter.parse({"tenants": "t_maha_power"})
    with pytest.raises(ValueError, match="expected integers"):
        Filter.parse({"tiers": ["one"]})


# ---------------------------------------------------------------- format
def test_the_veyra_block_comes_off_ulpf() -> None:
    rendered = ocsf_json(norm_event(tier=1, revision=2))
    assert rendered["veyra"] == {
        "tier": 1,
        "class": 3002,
        "tenant": "t_maha_power",
        "source": "src_authsrv_01",
        "revision": 2,
        "contract": "authsrv@2",
        SRC_IP_FIELD: "103.21.4.77",
    }
    # The event itself is untouched: Wazuh reads IF-NORM-EVENT, not a VEYRA dialect.
    assert rendered["class_uid"] == 3002
    assert rendered["ulpf"]["tier"] == 1


def test_every_tier_renders_including_the_unparseable_ones() -> None:
    for tier in (1, 2, 3, 4):
        rendered = ocsf_json(norm_event(tier=tier))
        assert rendered["veyra"]["tier"] == tier
        assert "tenant" in rendered["veyra"]


def test_a_tier_three_event_has_no_class_and_no_src_ip_field() -> None:
    """Tier 3 never assigns an endpoint (A4), so the flat convenience field is simply absent."""
    rendered = ocsf_json(norm_event(tier=3))
    assert rendered["veyra"]["class"] == 0
    assert SRC_IP_FIELD not in rendered["veyra"]


def test_an_unknown_format_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown format"):
        render("cef", norm_event())


# ---------------------------------------------------------------- masking
def test_hmac_is_deterministic_and_prefixed() -> None:
    key = b"k" * 32
    first = hmac_value("a.sharma", key)
    assert first == hmac_value("a.sharma", key), "a partner must be able to correlate"
    assert first.startswith("h_") and len(first) == 18
    assert "a.sharma" not in first


def test_two_routes_cannot_join_their_feeds(tmp_path: Path) -> None:
    """Per-route subkeys: the same user is a different h_… on each feed, by design."""
    master = ensure_route_key(tmp_path)
    one = hmac_value("a.sharma", route_key(master, "partner_a"))
    two = hmac_value("a.sharma", route_key(master, "partner_b"))
    assert one != two


def test_the_partner_plan_masks_the_user_and_removes_the_raw_bytes() -> None:
    plan = MaskingPlan.parse({"user.name": "hmac", "src_endpoint.ip": "hmac", "raw_data": "redact"})
    event = ocsf_json(norm_event())
    masked = plan.apply(event, b"k" * 32)

    assert masked["user"]["name"].startswith("h_")
    assert masked["src_endpoint"]["ip"].startswith("h_")
    assert masked["raw_data"] == REDACTED
    # The field is still there — a downstream schema check must not see a hole.
    assert "raw_data" in masked and "name" in masked["user"]


def test_a_masked_value_is_gone_from_every_field_it_appeared_in() -> None:
    """The bug A6's integration test caught: masking one path leaves the identity in `message`.

    A partner line with `user.name: h_…` beside `message: "user=a.sharma FAILED…"` protects nothing.
    """
    plan = MaskingPlan.parse({"user.name": "hmac"})
    event = ocsf_json(norm_event(user="a.sharma"))
    masked = plan.apply(event, b"k" * 32)

    placeholder = masked["user"]["name"]
    assert placeholder.startswith("h_")
    blob = json.dumps(masked)
    assert "a.sharma" not in blob, "the identity must be gone from the whole payload"
    # And it is the *same* placeholder everywhere, so the partner can still correlate.
    assert masked["message"] == f"user={placeholder} FAILED login from 103.21.4.77"
    assert masked["observables"][0]["value"] == placeholder


def test_a_very_short_value_is_masked_but_not_swept() -> None:
    """Replacing every "ab" in a log line would mangle text that is nobody's identity."""
    plan = MaskingPlan.parse({"user.name": "hmac"})
    event = ocsf_json(norm_event(user="ab"))
    masked = plan.apply(event, b"k" * 32)
    assert masked["user"]["name"].startswith("h_"), "the declared path is still masked"
    assert "ab" in masked["message"], "but the sweep leaves short values alone"


def test_masking_does_not_touch_the_event_other_routes_get() -> None:
    """The Wazuh route and the partner route render from the same event object."""
    plan = MaskingPlan.parse({"user.name": "hmac"})
    event = ocsf_json(norm_event())
    plan.apply(event, b"k" * 32)
    assert event["user"]["name"] == "a.sharma", "masking must copy, never mutate in place"


def test_masking_a_path_that_is_absent_is_not_an_error() -> None:
    plan = MaskingPlan.parse({"user.name": "hmac", "src_endpoint.ip": "hmac"})
    masked = plan.apply(ocsf_json(norm_event(tier=3)), b"k" * 32)  # tier 3 has neither
    assert "user" not in masked


def test_masking_none_is_a_no_op() -> None:
    assert MaskingPlan.parse("none").paths == ()
    event = ocsf_json(norm_event())
    assert MaskingPlan.parse("none").apply(event, b"k") is event


def test_an_unknown_masking_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown masking mode"):
        MaskingPlan.parse({"user.name": "encrypt"})


# ---------------------------------------------------------------- route compilation
def test_the_shipped_default_routes_compile(cfg: RouterSettings, tmp_path: Path) -> None:
    """The fallback document must always be loadable — it is what runs before C1 boots."""
    specs = load_routes_file(REPO / "services" / "router" / "routes.default.yaml")
    assert [spec.id for spec in specs] == ["wazuh_main", "partner_masked"]
    routes, errors = compile_routes(specs, master_key=b"k" * 32, cfg=cfg)
    assert not errors
    assert len(routes) == 2
    for route in routes:
        route.sink.close()


def test_the_default_routes_match_control_apis_seed() -> None:
    """If these drift, the router behaves differently depending on whether C1 is up."""
    ours = load_routes_file(REPO / "services" / "router" / "routes.default.yaml")
    theirs = load_routes_file(REPO / "services" / "control_api" / "seed" / "routes.yaml")
    assert [spec.model_dump() for spec in ours] == [spec.model_dump() for spec in theirs]


def test_one_bad_route_does_not_take_the_good_ones_down(cfg: RouterSettings) -> None:
    specs = [
        {"id": "good", "filter": {}, "sink": {"type": "ndjson_file", "path": "/tmp/x.ndjson"}},
        {"id": "bad", "filter": {}, "sink": {"type": "carrier_pigeon"}},
    ]
    routes, errors = compile_routes(specs, master_key=b"k" * 32, cfg=cfg)
    assert [route.id for route in routes] == ["good"]
    assert errors and "carrier_pigeon" in errors[0]
    routes[0].sink.close()


def test_a_route_payload_is_formatted_then_masked(cfg: RouterSettings, tmp_path: Path) -> None:
    route = compile_route(
        {
            "id": "partner_masked",
            "filter": {"tenants": ["t_maha_power"]},
            "format": "ocsf_json",
            "masking": {"user.name": "hmac", "raw_data": "redact"},
            "sink": {"type": "ndjson_file", "path": str(tmp_path / "p.ndjson")},
        },
        master_key=b"k" * 32,
        cfg=cfg,
    )
    payload = route.payload(norm_event())
    assert payload["veyra"]["tenant"] == "t_maha_power"
    assert payload["user"]["name"].startswith("h_")
    assert payload["raw_data"] == REDACTED
    route.sink.close()
