"""veyra_engine — the deterministic parse/normalize engine (IF-ENGINE-LIB, owner A).

S0 ships a **stub**: real signatures, real ``template_sig`` and ``extract_tokens``,
tier-4 output. A3 replaces the internals; nothing here may be renamed, because Track C
calls this library in-process for golden tests, backtests and onboarding previews.
"""

from veyra_common.hashing import template_sig

from veyra_engine.engine import (
    ENGINE_VERSION,
    Engine,
    backtest,
    decode,
    mask,
    provenance_check,
    serialize,
)
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

__version__ = ENGINE_VERSION

__all__ = [
    "ENGINE_VERSION",
    "BacktestResult",
    "Check",
    "Engine",
    "EngineContext",
    "Field",
    "NormResult",
    "PeelResult",
    "Token",
    "TokenKind",
    "backtest",
    "decode",
    "extract_tokens",
    "mask",
    "provenance_check",
    "serialize",
    "template_sig",
]
