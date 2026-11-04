"""Pipeline-independent names for fingerprinted input sets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from provium.input_types import input_type
from provium.semantic import canonical_hash

from provium_pipeline.store import ArtifactStore
from provium_pipeline.values import encode_value


def inspect_inputs(
    store: ArtifactStore,
    inputs: Mapping[str, Any],
    declarations: Mapping[str, str | dict[str, str]] | None = None,
) -> dict[str, Any]:
    if not inputs:
        raise ValueError("at least one named --input is required")
    result: dict[str, Any] = {}
    for name, location in inputs.items():
        declaration = declarations.get(name) if declarations is not None else None
        if declaration is None and not isinstance(location, str):
            declaration = (
                {"value_type": cast(dict[str, Any], location)["value_input"]["type"]}
                if isinstance(location, dict)
                and set(cast(dict[str, Any], location)) == {"value_input"}
                else {
                    "value_type": input_type(
                        cast(type[Any], type(cast(Any, location)))
                    ).name
                }
            )
        if isinstance(declaration, dict):
            result[name] = encode_value(declaration, location)
            continue
        try:
            artifact = store.inspect(cast(str, location))
        except (OSError, ValueError) as error:
            raise ValueError(f"input {name!r} ({location}): {error}") from error
        result[name] = {
            "location": artifact.location,
            "identity": artifact.identity,
            "fingerprint": artifact.fingerprint,
        }
    return result


def input_set_hash(inputs: Mapping[str, Any]) -> str:
    """Hash logical bindings and fingerprints, never paths or artifact UUIDs."""
    if not inputs:
        raise ValueError("an input set must contain at least one input")
    return canonical_hash(
        {
            "version": 1,
            "inputs": {
                name: item if "value_input" in item else item["fingerprint"]
                for name, item in inputs.items()
            },
        }
    )


def assert_input_set_name_is_valid(name: str) -> None:
    if not name or name != name.strip():
        raise ValueError(
            "input-set name must be nonempty without surrounding whitespace"
        )


def assert_input_set_selector_is_valid(name: str | None, key: str | None) -> None:
    if (name is None) == (key is None):
        raise ValueError("select an input set by name or hash")
