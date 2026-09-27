"""The one exception the compiler raises, carrying the YAML location when known."""

from __future__ import annotations


class ContractError(ValueError):
    """A Log Contract is invalid. ``line``/``column`` are 1-based YAML positions."""

    def __init__(self, message: str, *, line: int | None = None, column: int | None = None):
        self.message = message
        self.line = line
        self.column = column
        where = f"line {line}, column {column}: " if line is not None else ""
        super().__init__(f"{where}{message}")
