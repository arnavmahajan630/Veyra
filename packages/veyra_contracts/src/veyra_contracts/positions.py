"""Map every YAML value to its (line, column), so errors can point at the source."""

from __future__ import annotations

from collections.abc import Sequence

import yaml

YamlPath = tuple[str | int, ...]
Positions = dict[YamlPath, tuple[int, int]]


def node_positions(yaml_text: str) -> Positions:
    """1-based ``(line, column)`` of each value node, keyed by its path from the root."""
    root = yaml.compose(yaml_text, Loader=yaml.SafeLoader)
    out: Positions = {}
    if root is None:
        return out

    def walk(node: yaml.Node, path: YamlPath) -> None:
        out[path] = (node.start_mark.line + 1, node.start_mark.column + 1)
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                walk(value_node, (*path, str(key_node.value)))
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                walk(item, (*path, index))

    walk(root, ())
    return out


def locate(positions: Positions, path: Sequence[str | int]) -> tuple[int | None, int | None]:
    """The position of ``path``, or of its nearest ancestor that has one."""
    probe: YamlPath = tuple(path)
    while probe:
        if probe in positions:
            return positions[probe]
        probe = probe[:-1]
    return positions.get((), (None, None))
