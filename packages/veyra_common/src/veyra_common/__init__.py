"""veyra_common — the shared foundation every VEYRA service builds on.

Owned by Person A in S0, then edited by each track via PR for its own records.
See docs/plan/02_CONTRACTS.md for the interfaces these modules implement.
"""

from veyra_common.envelope import rfc3339_ns, stamp, stamp_framed
from veyra_common.framing import FramedEvent, frame_datagram, is_continuation, split_lines
from veyra_common.hashing import sha256_bytes, sha256_hex, template_sig, template_sig_parts
from veyra_common.ids import now_ns, uuid7, uuid7_str
from veyra_common.settings import Settings, get_settings, settings

__version__ = "0.1.0"

__all__ = [
    "FramedEvent",
    "Settings",
    "frame_datagram",
    "get_settings",
    "is_continuation",
    "now_ns",
    "rfc3339_ns",
    "settings",
    "sha256_bytes",
    "sha256_hex",
    "split_lines",
    "stamp",
    "stamp_framed",
    "template_sig",
    "template_sig_parts",
    "uuid7",
    "uuid7_str",
]
