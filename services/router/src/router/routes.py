"""A route: filter, format, masking, sink — compiled once, evaluated per event (IF-ROUTES).

Parsing is strict and happens at load. A route with an unknown sink type, an unknown masking mode or
a misspelled filter key is **refused**, and the router keeps serving the routes it already had. The
alternative — accept it and fail per event — is how one typo becomes "the partner feed silently
received everything".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from router.filters import Filter
from router.formats import render
from router.keys import route_key
from router.masking import MaskingPlan
from router.sinks import Sink, build_sink
from veyra_common.models import RouteSpec

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Route:
    """One compiled route."""

    id: str
    filter: Filter
    format: str
    masking: MaskingPlan
    sink: Sink
    key: bytes

    def accepts(self, event: dict[str, Any]) -> bool:
        return self.filter.accepts(event)

    def payload(self, event: dict[str, Any]) -> dict[str, Any]:
        """The bytes-to-be for this route: formatted, then masked."""
        return self.masking.apply(render(self.format, event), self.key)


def compile_route(spec: RouteSpec | dict[str, Any], *, master_key: bytes, cfg: Any) -> Route:
    parsed = spec if isinstance(spec, RouteSpec) else RouteSpec.model_validate(spec)
    return Route(
        id=parsed.id,
        filter=Filter.parse(parsed.filter),
        format=parsed.format,
        masking=MaskingPlan.parse(parsed.masking),
        sink=build_sink(parsed.sink, cfg=cfg),
        key=route_key(master_key, parsed.id),
    )


def compile_routes(
    specs: list[RouteSpec] | list[dict[str, Any]], *, master_key: bytes, cfg: Any
) -> tuple[list[Route], list[str]]:
    """``(routes, errors)`` — one bad route never stops the good ones from being served."""
    routes: list[Route] = []
    errors: list[str] = []
    for spec in specs:
        try:
            route = compile_route(spec, master_key=master_key, cfg=cfg)
        except Exception as exc:
            route_id = spec.id if isinstance(spec, RouteSpec) else spec.get("id", "?")
            errors.append(f"{route_id}: {exc}")
            continue
        # `render` is checked here so an unknown format fails at load like everything else.
        render(route.format, {"ulpf": {}})
        routes.append(route)
    return routes, errors


def load_routes_file(path: Any) -> list[RouteSpec]:
    """The fallback document, used only when the `routes` control key is absent."""
    from pathlib import Path

    import yaml

    from veyra_common.models import RoutesMessage

    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return list(RoutesMessage.model_validate(document).routes)
