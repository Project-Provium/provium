"""Immutable identifiers used by the pipeline domain."""

from __future__ import annotations

import re
from typing import Self
from uuid import UUID, uuid4

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class _TextIdentifier(str):
    def __new__(cls, value: str) -> Self:
        if isinstance(value, cls):
            return value
        if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
            raise ValueError(f"{cls.__name__} must be a valid identifier")
        return str.__new__(cls, value)


class PipelineIdentifier(_TextIdentifier):
    """Stable user-facing pipeline identifier."""


class PipelineVersion(_TextIdentifier):
    """Stable user-facing pipeline version."""


class PipelineNodeIdentifier(_TextIdentifier):
    """Pipeline-local node identifier."""


class PipelineInputName(_TextIdentifier):
    """Pipeline input name."""


class PipelineOutputName(_TextIdentifier):
    """Pipeline output name."""


class InputRecordKey(_TextIdentifier):
    """Stable key for one input record."""


class InputSetIdentifier(_TextIdentifier):
    """Stable name for an immutable input set."""


class StoreIdentifier(_TextIdentifier):
    """Stable artifact or execution store identifier."""


class _UUIDIdentifier:
    __slots__ = ("_value",)

    def __init__(self, value: UUID) -> None:
        if not isinstance(value, UUID):
            raise TypeError(f"{type(self).__name__} value must be a UUID")
        object.__setattr__(self, "_value", value)

    @property
    def value(self) -> UUID:
        return self._value

    @classmethod
    def new(cls) -> Self:
        return cls(uuid4())

    @classmethod
    def parse(cls, value: str) -> Self:
        return cls(UUID(value))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __str__(self) -> str:
        return str(self._value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str(self)!r})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, _UUIDIdentifier)
            and type(self) is type(other)
            and str(self._value) == str(other._value)
        )

    def __hash__(self) -> int:
        return hash((type(self), self._value))


class RunId(_UUIDIdentifier):
    """Unique run identifier."""


class DispatchId(_UUIDIdentifier):
    """Unique dispatch identifier."""


class TaskId(_UUIDIdentifier):
    """Unique task identifier."""


class AttemptId(_UUIDIdentifier):
    """Unique task-attempt identifier."""


class ComputationKey(str):
    """Normalized SHA-256 identity for equivalent computation."""

    def __new__(cls, value: str) -> Self:
        normalized = value.lower()
        if _SHA256_PATTERN.fullmatch(normalized) is None:
            raise ValueError("ComputationKey must be a SHA-256 hexadecimal digest")
        return str.__new__(cls, normalized)


__all__ = [
    "AttemptId",
    "ComputationKey",
    "DispatchId",
    "InputRecordKey",
    "InputSetIdentifier",
    "PipelineIdentifier",
    "PipelineInputName",
    "PipelineNodeIdentifier",
    "PipelineOutputName",
    "PipelineVersion",
    "RunId",
    "StoreIdentifier",
    "TaskId",
]
