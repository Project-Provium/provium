"""Value input types and explicit codecs for external representations."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from typing import Any, cast

from provium.files import FileLocation, normalize_location


@dataclass(frozen=True, slots=True, init=False)
class FilePath(os.PathLike[str]):
    """A local or S3 location; construction performs no file access."""

    location: str

    def __init__(self, location: FileLocation) -> None:
        raw = os.fspath(location)
        if not raw.strip():
            raise ValueError("file path must be nonempty")
        object.__setattr__(self, "location", str(normalize_location(raw)))

    def __fspath__(self) -> str:
        return self.location

    def __str__(self) -> str:
        return self.location


@dataclass(frozen=True, slots=True)
class InputType[T]:
    """Typed codec; Python calls validate instances, external calls decode."""

    python_type: type[T]
    name: str
    decode: Callable[[Any], T]
    encode: Callable[[T], object]

    def validate(self, value: object) -> T:
        if self.python_type is int:
            if type(value) is not int:
                raise TypeError("expected an integer")
        elif self.python_type is float:
            if type(value) not in (int, float):
                raise TypeError("expected a float")
            try:
                value = float(cast(int | float, value))
            except OverflowError as error:
                raise ValueError("expected a finite float") from error
            if not isfinite(value):
                raise ValueError("expected a finite float")
        elif self.python_type is FilePath and isinstance(value, (str, os.PathLike)):
            value = FilePath(cast(FileLocation, value))
        if not isinstance(value, self.python_type):
            raise TypeError(f"expected {self.python_type.__name__}")
        return cast(T, value)

    def parse(self, value: object) -> T:
        """Decode external data, then apply the same runtime validation."""
        return self.validate(self.decode(value))

    def snapshot(self, value: object) -> object:
        """Return an isolated JSON value suitable for execution provenance."""
        return json.loads(
            json.dumps(self.encode(self.validate(value)), allow_nan=False)
        )


_INPUT_TYPES: dict[type[Any], InputType[Any]] = {}


def register_input_type[T](
    python_type: type[T],
    *,
    name: str,
    decode: Callable[[Any], T],
    encode: Callable[[T], object],
) -> InputType[T]:
    """Register an immutable codec before declaring fields of a custom type."""
    if not isinstance(python_type, type):
        raise TypeError("input type must be a Python type")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("input type name must be nonempty")
    if not callable(decode) or not callable(encode):
        raise TypeError("input type encode and decode must be callable")
    if python_type in _INPUT_TYPES or any(
        codec.name == name for codec in _INPUT_TYPES.values()
    ):
        raise ValueError(f"input type already registered: {name}")
    codec = InputType(python_type, name, decode, encode)
    _INPUT_TYPES[python_type] = codec
    return codec


def input_type[T](python_type: type[T]) -> InputType[T]:
    """Resolve a declaration without executing its constructor."""
    try:
        return _INPUT_TYPES[python_type]
    except KeyError:
        raise TypeError(
            f"unregistered input type: {python_type!r}; use register_input_type"
        ) from None


def _decode_integer(value: object) -> int:
    if isinstance(value, str):
        return int(value)
    if type(value) is not int:
        raise TypeError("expected an integer")
    return value


def _decode_float(value: object) -> float:
    if isinstance(value, str):
        return float(value)
    return input_type(float).validate(value)


register_input_type(int, name="int", decode=_decode_integer, encode=lambda value: value)
register_input_type(
    float, name="float", decode=_decode_float, encode=lambda value: value
)
register_input_type(FilePath, name="file-path", decode=FilePath, encode=str)

__all__ = ["FilePath", "InputType", "register_input_type"]
