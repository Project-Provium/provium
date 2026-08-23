from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from provium import JsonValue

from .identifiers import InputRecordKey


@dataclass(frozen=True, slots=True, init=False)
class InputRecord:
    """One immutable ordered set of record-scoped artifact bindings."""

    key: InputRecordKey
    inputs: Mapping[str, tuple[str, ...]]
    labels: Mapping[str, JsonValue]

    def __init__(
        self,
        *,
        key: InputRecordKey,
        inputs: Mapping[str, Sequence[str]],
        labels: Mapping[str, JsonValue],
    ) -> None:
        object.__setattr__(self, "key", key)
        object.__setattr__(
            self,
            "inputs",
            MappingProxyType({name: tuple(values) for name, values in inputs.items()}),
        )
        object.__setattr__(self, "labels", MappingProxyType(dict(labels)))
