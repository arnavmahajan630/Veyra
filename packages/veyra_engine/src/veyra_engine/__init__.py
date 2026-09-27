"""veyra_engine — the deterministic parse/normalize engine (IF-ENGINE-LIB, owner A).

S0 ships a **stub**: real signatures, real ``template_sig`` and ``extract_tokens``,
tier-4 output. A3 replaces the internals; nothing here may be renamed, because Track C
calls this library in-process for golden tests, backtests and onboarding previews.
"""

from veyra_common.hashing import template_sig
from veyra_engine.decode import Decoded, decode
from veyra_engine.engine import (
    ENGINE_VERSION,
    Engine,
    backtest,
    provenance_check,
    serialize,
)
from veyra_engine.mask import mask
from veyra_engine.peel import run_layers
from veyra_engine.spanjson import scan as scan_json
from veyra_engine.template import compile_pattern, compile_template
from veyra_engine.testing import mini_compile
from veyra_engine.timeparse import parse_time
from veyra_engine.tokens import extract_tokens
from veyra_engine.types import (
    BacktestResult,
    Check,
    EngineContext,
    Field,
    NormResult,
    PeelResult,
    Token,
    TokenKind,
)
from veyra_engine.validate import ocsf_version, validate_event

__version__ = ENGINE_VERSION

__all__ = [
    "ENGINE_VERSION",
    "BacktestResult",
    "Check",
    "Decoded",
    "Engine",
    "EngineContext",
    "Field",
    "NormResult",
    "PeelResult",
    "Token",
    "TokenKind",
    "backtest",
    "compile_pattern",
    "compile_template",
    "decode",
    "extract_tokens",
    "mask",
    "mini_compile",
    "ocsf_version",
    "parse_time",
    "provenance_check",
    "run_layers",
    "scan_json",
    "serialize",
    "template_sig",
    "validate_event",
]
