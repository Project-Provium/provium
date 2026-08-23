"""Structured pipeline reference values and shorthand parsing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..identifiers import (
    PipelineInputName,
    PipelineNodeIdentifier,
    PipelineOutputName,
)


@dataclass(frozen=True, slots=True)
class PipelineInputReference:
    """Reference to a declared pipeline input."""

    name: PipelineInputName

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", PipelineInputName(self.name))

    def __str__(self) -> str:
        return f"$inputs.{self.name}"


@dataclass(frozen=True, slots=True)
class NodeOutputReference:
    """Reference to one output field of an upstream node."""

    node: PipelineNodeIdentifier
    output: PipelineOutputName

    def __post_init__(self) -> None:
        object.__setattr__(self, "node", PipelineNodeIdentifier(self.node))
        object.__setattr__(self, "output", PipelineOutputName(self.output))

    def __str__(self) -> str:
        return f"$nodes.{self.node}.{self.output}"


Reference = PipelineInputReference | NodeOutputReference
ReferenceValue = Reference | tuple[Reference, ...]


def parse_reference(value: str) -> Reference:
    """Parse one supported pipeline-reference shorthand."""

    try:
        if value.startswith("$inputs."):
            name = value.removeprefix("$inputs.")
            if "." not in name:
                return PipelineInputReference(PipelineInputName(name))
        if value.startswith("$nodes."):
            parts = value.removeprefix("$nodes.").split(".")
            if len(parts) == 2:
                return NodeOutputReference(
                    PipelineNodeIdentifier(parts[0]), PipelineOutputName(parts[1])
                )
    except ValueError:
        pass
    raise ValueError(f"{value!r} is not a supported pipeline reference")


def parse_reference_value(value: str | Sequence[str]) -> ReferenceValue:
    """Parse a scalar or ordered repeated pipeline binding."""

    if isinstance(value, str):
        return parse_reference(value)
    return tuple(parse_reference(item) for item in value)


__all__ = [
    "NodeOutputReference",
    "PipelineInputReference",
    "Reference",
    "ReferenceValue",
    "parse_reference",
    "parse_reference_value",
]
