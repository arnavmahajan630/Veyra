"""VEYRA Log Contracts: models, compiler (C2) and, later, golden tests and drafting."""

from veyra_contracts.compiler import (
    COMPILER_VERSION,
    EPOCH,
    CompiledContract,
    CompiledTemplate,
    MapEntry,
    compile,
)
from veyra_contracts.errors import ContractError
from veyra_contracts.models import ContractYaml

__version__ = "0.1.0"

__all__ = [
    "COMPILER_VERSION",
    "EPOCH",
    "CompiledContract",
    "CompiledTemplate",
    "ContractError",
    "ContractYaml",
    "MapEntry",
    "compile",
]
