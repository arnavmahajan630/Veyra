"""ContractError formatting and the YAML position index used for error locations."""

from __future__ import annotations

import pytest
import yaml

from veyra_contracts.errors import ContractError
from veyra_contracts.positions import locate, node_positions

TEXT = "contract: x\ntemplates:\n  - id: a\n    class: authentication\n"


def test_contract_error_formats_its_location() -> None:
    err = ContractError("bad thing", line=3, column=7)
    assert str(err) == "line 3, column 7: bad thing"
    assert (err.message, err.line, err.column) == ("bad thing", 3, 7)


def test_contract_error_without_a_location_is_just_the_message() -> None:
    err = ContractError("bad thing")
    assert str(err) == "bad thing"
    assert err.line is None and err.column is None


def test_positions_are_one_based_line_and_column_of_values() -> None:
    pos = node_positions(TEXT)
    assert pos[()] == (1, 1)
    assert pos[("contract",)] == (1, 11)
    assert pos[("templates", 0, "class")] == (4, 12)


def test_locate_falls_back_to_the_nearest_known_parent() -> None:
    pos = node_positions(TEXT)
    assert locate(pos, ("templates", 0, "class", "ConstValue")) == (4, 12)
    assert locate(pos, ("templates", 0, "missing")) == pos[("templates", 0)]
    assert locate(pos, ("nope",)) == (1, 1)


def test_bad_yaml_raises_yaml_error() -> None:
    with pytest.raises(yaml.YAMLError):
        node_positions("templates: [\n")
