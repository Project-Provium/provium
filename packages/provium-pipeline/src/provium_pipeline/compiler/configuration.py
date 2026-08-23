"""Strict pipeline configuration documents and codecs."""

from __future__ import annotations

import json
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from yaml import YAMLError

from provium import JsonValue


class PipelineConfiguration(BaseModel):
    """One ordered external pipeline configuration layer."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["provium.pipeline-config/v1"] = Field(
        default="provium.pipeline-config/v1",
        alias="schema",
        serialization_alias="schema",
    )
    nodes: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)


class PipelineConfigurationLoadError(ValueError):
    """Raised when a pipeline configuration document cannot be loaded."""

    def __init__(self, source: str, error: Exception) -> None:
        self.source = source
        self.error = error
        super().__init__(
            f"failed to load pipeline configuration from {source}: {error}"
        )


def load_pipeline_configuration_json(
    document: str, *, source: str | None = None
) -> PipelineConfiguration:
    """Load and validate a JSON pipeline configuration document."""
    location = source or "<json>"
    try:
        value = json.loads(document)
        return _validate_configuration(value)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as error:
        raise PipelineConfigurationLoadError(location, error) from error


def load_pipeline_configuration_yaml(
    document: str, *, source: str | None = None
) -> PipelineConfiguration:
    """Load and validate a YAML pipeline configuration document."""
    location = source or "<yaml>"
    try:
        value = yaml.safe_load(document)
        return _validate_configuration(value)
    except (YAMLError, ValidationError, TypeError, ValueError) as error:
        raise PipelineConfigurationLoadError(location, error) from error


def canonical_configuration_document(
    configuration: PipelineConfiguration,
) -> dict[str, JsonValue]:
    """Return the complete canonical JSON-domain configuration document."""
    return configuration.model_dump(mode="json", by_alias=True)


def _validate_configuration(value: Any) -> PipelineConfiguration:
    if not isinstance(value, dict):
        raise TypeError("pipeline configuration document must be a mapping")
    return PipelineConfiguration.model_validate(value)
