"""Procedure configuration models."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
from os import PathLike
from typing import Never, Protocol, cast

from pydantic import BaseModel, ConfigDict

from provium.files import local_file

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def _stabilize_serialized_value(original: object, serialized: object) -> object:
    if isinstance(original, (set, frozenset)) and isinstance(serialized, list):
        collection = cast(set[object] | frozenset[object], original)
        serialized_items = cast(list[object], serialized)
        if len(collection) != len(serialized_items):
            return serialized_items
        stabilized = [
            _stabilize_serialized_value(item, serialized_item)
            for item, serialized_item in zip(collection, serialized_items, strict=True)
        ]
        return sorted(
            stabilized,
            key=_canonical_json_bytes,
        )
    if isinstance(original, Mapping) and isinstance(serialized, Mapping):
        original_mapping = cast(Mapping[object, object], original)
        serialized_mapping = cast(Mapping[object, object], serialized)
        if len(original_mapping) != len(serialized_mapping):
            return serialized_mapping
        matching_keys = [key for key in serialized_mapping if key in original_mapping]
        if not matching_keys and len(original_mapping) == 1:
            original_item = next(iter(original_mapping.values()))
            serialized_key, serialized_item = next(iter(serialized_mapping.items()))
            return {
                serialized_key: _stabilize_serialized_value(
                    original_item, serialized_item
                )
            }
        return {
            key: (
                _stabilize_serialized_value(original_mapping[key], item)
                if key in original_mapping
                else item
            )
            for key, item in serialized_mapping.items()
        }
    if isinstance(original, (list, tuple)) and isinstance(serialized, list):
        original_sequence = cast(list[object] | tuple[object, ...], original)
        serialized_sequence = cast(list[object], serialized)
        if len(original_sequence) != len(serialized_sequence):
            return serialized_sequence
        return [
            _stabilize_serialized_value(item, serialized_item)
            for item, serialized_item in zip(
                original_sequence, serialized_sequence, strict=True
            )
        ]
    return serialized


class ProcedureConfig(BaseModel):
    """Immutable, strictly validated configuration for a procedure."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    """Canonical configuration values and their stable identities."""

    model_target: str
    schema_digest: str
    value: JsonValue
    value_digest: str

    @classmethod
    def from_configuration(
        cls, configuration: ProcedureConfig
    ) -> ConfigurationSnapshot:
        """Create a deterministic snapshot of a validated configuration."""
        configuration_type = type(configuration)
        python_value = configuration.model_dump(
            mode="python",
            by_alias=True,
            exclude_none=False,
            exclude_defaults=False,
            exclude_unset=False,
        )
        _assert_numbers_are_finite(python_value)
        serialized_value = configuration.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=False,
            exclude_defaults=False,
            exclude_unset=False,
        )
        value, value_digest = _canonicalize(
            _stabilize_serialized_value(python_value, serialized_value)
        )
        _, schema_digest = _canonicalize(configuration_type.model_json_schema())
        return cls(
            model_target=(
                f"{configuration_type.__module__}:{configuration_type.__qualname__}"
            ),
            schema_digest=schema_digest,
            value=value,
            value_digest=value_digest,
        )


class _YamlModule(Protocol):
    def safe_load(self, stream: str) -> object: ...


def compose_configuration(
    layers: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Merge ordered raw configuration layers without mutating their values."""
    composed: dict[str, object] = {}
    for layer in layers:
        composed = _merge_mappings(composed, layer)
    return composed


def load_json_configuration(path: str | PathLike[str]) -> dict[str, object]:
    """Load one raw configuration layer from a UTF-8 JSON object."""
    configuration_path = path
    with local_file(path) as local:
        document = local.read_text(encoding="utf-8")

    def reject_non_finite(constant: str) -> Never:
        raise json.JSONDecodeError(
            f"non-finite number {constant} is not valid JSON", document, 0
        )

    value = cast(
        object,
        json.loads(document, parse_constant=reject_non_finite),
    )
    if not isinstance(value, dict):
        raise TypeError(f"configuration root must be an object: {configuration_path}")
    return cast(dict[str, object], value)


def load_yaml_configuration(path: str | PathLike[str]) -> dict[str, object]:
    """Load one raw configuration layer from a UTF-8 YAML mapping."""
    try:
        yaml = cast(_YamlModule, import_module("yaml"))
    except ModuleNotFoundError as error:
        if error.name != "yaml":
            raise
        raise RuntimeError(
            "YAML configuration support requires installing provium[yaml]"
        ) from error

    configuration_path = path
    with local_file(path) as local:
        value = yaml.safe_load(local.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise TypeError(f"configuration root must be a mapping: {configuration_path}")
    mapping = cast(Mapping[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise TypeError(
            f"configuration root keys must be strings: {configuration_path}"
        )
    return dict(cast(Mapping[str, object], mapping))


def _merge_mappings(
    earlier: Mapping[str, object],
    later: Mapping[str, object],
) -> dict[str, object]:
    merged = _copy_mapping(earlier)
    for key, later_value in later.items():
        earlier_value = merged.get(key)
        if isinstance(earlier_value, Mapping) and isinstance(later_value, Mapping):
            merged[key] = _merge_mappings(
                cast(Mapping[str, object], earlier_value),
                cast(Mapping[str, object], later_value),
            )
        else:
            merged[key] = _copy_value(later_value)
    return merged


def _copy_mapping(
    value: Mapping[str, object], active: set[int] | None = None
) -> dict[str, object]:
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ValueError("configuration must not contain a recursive mapping")
    active.add(identity)
    try:
        return {key: _copy_value(item, active) for key, item in value.items()}
    finally:
        active.remove(identity)


def _copy_value(value: object, active: set[int] | None = None) -> object:
    if isinstance(value, Mapping):
        return _copy_mapping(cast(Mapping[str, object], value), active)
    if isinstance(value, (list, tuple)):
        sequence_value = cast(list[object] | tuple[object, ...], value)
        active = set() if active is None else active
        identity = id(sequence_value)
        if identity in active:
            raise ValueError("configuration must not contain a recursive sequence")
        active.add(identity)
        try:
            if isinstance(sequence_value, list):
                return [_copy_value(item, active) for item in sequence_value]
            return tuple(_copy_value(item, active) for item in sequence_value)
        finally:
            active.remove(identity)
    return deepcopy(value)


def _canonicalize(value: object) -> tuple[JsonValue, str]:
    encoded = _canonical_json_bytes(value)
    normalized = cast(JsonValue, json.loads(encoded))
    return normalized, sha256(encoded).hexdigest()


def _assert_numbers_are_finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("configuration snapshot must contain finite JSON numbers")
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        for key, item in mapping.items():
            _assert_numbers_are_finite(key)
            _assert_numbers_are_finite(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        collection = cast(
            list[object] | tuple[object, ...] | set[object] | frozenset[object],
            value,
        )
        for item in collection:
            _assert_numbers_are_finite(item)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


__all__ = [
    "ConfigurationSnapshot",
    "JsonValue",
    "ProcedureConfig",
    "compose_configuration",
    "load_json_configuration",
    "load_yaml_configuration",
]
