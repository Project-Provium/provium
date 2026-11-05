"""Typed JSON values shared by execution, cache lookup, and durable queues."""

from __future__ import annotations

from typing import Any, cast

from provium.input_types import InputType, input_type, named_input_type


def value_declaration(value: Any) -> str | dict[str, str]:
    """Keep artifact identifiers distinct from registered value codec names."""
    if isinstance(value, (str, dict)):
        return cast(str | dict[str, str], value)
    if isinstance(value, InputType):
        return {"value_type": value.name}
    if isinstance(value, type):
        return {"value_type": input_type(value).name}
    return value.identifier


def encode_value(declaration: dict[str, str], value: Any) -> dict[str, Any]:
    """Validate and isolate a value, or restore an already encoded snapshot."""
    codec = named_input_type(declaration["value_type"])
    if isinstance(value, dict) and set(cast(dict[str, Any], value)) == {"value_input"}:
        payload: Any = cast(dict[str, Any], value)["value_input"]
        if payload["type"] != codec.name:
            raise ValueError("incompatible value input type")
        value = None if payload["value"] is None else codec.parse(payload["value"])
    elif isinstance(value, str):
        value = codec.parse(value)
    return {
        "value_input": {
            "type": codec.name,
            "value": None if value is None else codec.snapshot(cast(Any, value)),
        }
    }
