from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from provium import JsonValue, canonical_digest

from ..identifiers import InputRecordKey, InputSetIdentifier


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


@dataclass(frozen=True, slots=True, init=False)
class InputSet:
    """An immutable named snapshot of input records."""

    identity: str
    identifier: InputSetIdentifier
    records: tuple[InputRecord, ...]
    digest: str
    created_at: datetime
    metadata: Mapping[str, JsonValue]

    def __init__(
        self,
        *,
        identity: str,
        identifier: InputSetIdentifier,
        records: Sequence[InputRecord],
        digest: str,
        created_at: datetime,
        metadata: Mapping[str, JsonValue],
    ) -> None:
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "identifier", identifier)
        object.__setattr__(self, "records", tuple(records))
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "metadata", MappingProxyType(dict(metadata)))


class InputSourceKind(StrEnum):
    """Supported origins for frozen input records."""

    DIRECT = "direct"
    NDJSON = "ndjson"
    INPUT_SET = "input-set"
    ITERABLE = "iterable"
    RESOLVER = "resolver"


@dataclass(frozen=True, slots=True, init=False)
class InputSourceDescriptor:
    """Reproducibility metadata for one resolved input source."""

    kind: InputSourceKind
    identifier: str
    configuration: Mapping[str, JsonValue]
    result_digest: str

    def __init__(
        self,
        *,
        kind: InputSourceKind,
        identifier: str,
        configuration: Mapping[str, JsonValue],
        result_digest: str,
    ) -> None:
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "identifier", identifier)
        object.__setattr__(
            self, "configuration", MappingProxyType(dict(configuration))
        )
        object.__setattr__(self, "result_digest", result_digest)


@dataclass(frozen=True, slots=True, init=False)
class RunInputSnapshot:
    """Canonical exact inputs frozen before a run is created."""

    records: tuple[InputRecord, ...]
    shared_inputs: Mapping[str, tuple[str, ...]]
    digest: str
    sources: tuple[InputSourceDescriptor, ...]

    def __init__(
        self,
        *,
        records: Sequence[InputRecord],
        shared_inputs: Mapping[str, Sequence[str]],
        digest: str,
        sources: Sequence[InputSourceDescriptor],
    ) -> None:
        object.__setattr__(self, "records", tuple(records))
        object.__setattr__(
            self,
            "shared_inputs",
            MappingProxyType(
                {name: tuple(values) for name, values in shared_inputs.items()}
            ),
        )
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "sources", tuple(sources))

    @classmethod
    def create(
        cls,
        *,
        records: Sequence[InputRecord],
        shared_inputs: Mapping[str, Sequence[str]],
        sources: Sequence[InputSourceDescriptor],
    ) -> RunInputSnapshot:
        ordered_records = tuple(sorted(records, key=lambda record: record.key))
        keys = [record.key for record in ordered_records]
        for previous, current in zip(keys, keys[1:], strict=False):
            if previous == current:
                raise ValueError(f"duplicate input record key: {current}")
        frozen_shared = {
            name: tuple(values) for name, values in sorted(shared_inputs.items())
        }
        document: JsonValue = {
            "records": [
                {
                    "key": str(record.key),
                    "inputs": {
                        name: list(values)
                        for name, values in sorted(record.inputs.items())
                    },
                    "labels": dict(record.labels),
                }
                for record in ordered_records
            ],
            "shared_inputs": {
                name: list(values) for name, values in frozen_shared.items()
            },
        }
        return cls(
            records=ordered_records,
            shared_inputs=frozen_shared,
            digest=canonical_digest(document),
            sources=sources,
        )
