"""JSON/YAML codecs and source-aware definition diagnostics."""

from __future__ import annotations

import json
from typing import Any, cast

import yaml
from pydantic import BaseModel, ValidationError

from provium import JsonValue

from .models import PipelineDefinition
from .references import Reference


class PipelineDefinitionLoadError(ValueError):
    """A source-aware pipeline definition parsing or validation failure."""


def canonical_definition_document(
    definition: PipelineDefinition,
) -> dict[str, JsonValue]:
    """Return the complete validated definition in the core JSON domain."""

    return cast(dict[str, JsonValue], _json_value(definition))


def load_pipeline_json(content: str, *, source: str = "<json>") -> PipelineDefinition:
    """Load and validate a JSON pipeline definition."""

    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise PipelineDefinitionLoadError(f"{source}:$: {error.msg}") from error
    return _validate(value, source)


def load_pipeline_yaml(content: str, *, source: str = "<yaml>") -> PipelineDefinition:
    """Load and validate a YAML pipeline definition."""

    try:
        value = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise PipelineDefinitionLoadError(f"{source}:$: invalid YAML") from error
    return _validate(value, source)


def _validate(value: Any, source: str) -> PipelineDefinition:
    try:
        return PipelineDefinition.model_validate(value)
    except ValidationError as error:
        first = error.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "$"
        raise PipelineDefinitionLoadError(
            f"{source}:{location}: {first['msg']}"
        ) from error


def _json_value(value: Any) -> Any:
    if isinstance(value, Reference):
        return str(value)
    if isinstance(value, BaseModel):
        return {
            (field.serialization_alias or field.alias or name): _json_value(
                getattr(value, name)
            )
            for name, field in type(value).model_fields.items()
        }
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {str(key): _json_value(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        return [_json_value(item) for item in sequence]
    return value


__all__ = [
    "PipelineDefinitionLoadError",
    "canonical_definition_document",
    "load_pipeline_json",
    "load_pipeline_yaml",
]
