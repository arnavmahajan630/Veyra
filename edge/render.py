"""Render `edge/vector/vector.toml.tmpl` into one config per zone.

Why render instead of letting Vector interpolate `${VAR}` itself: the limits belong to the
profile, and rendering makes the effective values visible in a file we can diff, lint and
test — `vector validate` then checks exactly what will run. `make edge-render` (called by
`make up`) regenerates both files, so changing a knob and re-running `make up` is enough.

    uv run python edge/render.py            # writes vector-dmz.toml and vector-core.toml
    uv run python edge/render.py --check    # fails if the rendered files are out of date
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from string import Template

from veyra_common.settings import Settings

EDGE = Path(__file__).resolve().parent
TEMPLATE = EDGE / "vector" / "vector.toml.tmpl"

# Vector refuses a disk buffer smaller than 256 MiB + 32 bytes of its own bookkeeping
# ("parameter 'max_buffer_size' was invalid"). Clamp rather than let a profile value
# silently crash-loop the edge.
MIN_DISK_BUFFER_BYTES = 268_435_488


@dataclass(frozen=True, slots=True)
class ZoneSpec:
    """One zone's listeners and identity. Ports come from IF-PORTS."""

    zone: str
    collector_id: str
    udp_port: int
    tcp_port: int

    @property
    def listener_udp(self) -> str:
        return f"{self.zone}-udp"

    @property
    def listener_tcp(self) -> str:
        return f"{self.zone}-tcp"

    @property
    def filename(self) -> str:
        return f"vector-{self.zone}.toml"


# IF-PORTS: 5514/udp + 5515/tcp for the dmz edge, 5524/udp + 5525/tcp for the core edge.
ZONES: tuple[ZoneSpec, ...] = (
    ZoneSpec(zone="dmz", collector_id="edge-dmz-01", udp_port=5514, tcp_port=5515),
    ZoneSpec(zone="core", collector_id="edge-core-01", udp_port=5524, tcp_port=5525),
)


def render(spec: ZoneSpec, cfg: Settings) -> str:
    """Substitute one zone's values into the template."""
    template = Template(TEMPLATE.read_text())
    return template.substitute(
        ZONE=spec.zone,
        COLLECTOR_ID=spec.collector_id,
        UDP_PORT=spec.udp_port,
        TCP_PORT=spec.tcp_port,
        LISTENER_UDP=spec.listener_udp,
        LISTENER_TCP=spec.listener_tcp,
        MAX_EVENT_BYTES=cfg.max_event_bytes,
        MULTILINE_FLUSH_MS=cfg.edge_multiline_flush_ms,
        EDGE_BUFFER_BYTES=max(cfg.edge_buffer_bytes, MIN_DISK_BUFFER_BYTES),
        # Inside the compose network the broker is kafka:9092; a remote edge overrides
        # VEYRA_KAFKA_BOOTSTRAP.
        KAFKA_BOOTSTRAP=cfg.kafka_bootstrap,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if a rendered file differs from the template + current settings",
    )
    args = parser.parse_args()

    cfg = Settings()
    stale: list[str] = []
    for spec in ZONES:
        target = EDGE / "vector" / spec.filename
        content = render(spec, cfg)
        if args.check:
            current = target.read_text() if target.exists() else ""
            if current != content:
                stale.append(spec.filename)
            continue
        target.write_text(content)
        print(f"rendered {target.relative_to(EDGE.parent)}")

    if args.check:
        if stale:
            print(f"stale rendered configs: {', '.join(stale)} — run `make edge-render`")
            return 1
        print("edge configs are up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
