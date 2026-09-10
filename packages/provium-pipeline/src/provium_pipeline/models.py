"""Versioned, YAML-compatible pipeline definitions."""

from collections.abc import Mapping
from os import PathLike
from typing import Annotated

from provium import load_yaml_configuration
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")]


class PipelineStep(BaseModel):
    """A procedure invocation; port names belong to its procedure contract."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    procedure: str = Field(min_length=1)
    configuration: dict[str, object] = Field(default_factory=dict)
    setup_inputs: dict[Name, str | list[str]] = Field(default_factory=dict)
    inputs: dict[Name, str | list[str]] = Field(default_factory=dict)


class Pipeline(BaseModel):
    """A definition independent of any particular execution or storage policy."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    name: Name
    configuration: dict[str, object] = Field(default_factory=dict)
    inputs: list[Name] = Field(default_factory=list)
    steps: dict[Name, PipelineStep] = Field(min_length=1)
    outputs: dict[Name, str] = Field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "Pipeline":
        """Parse a definition; plan() validates procedures and connections."""
        return cls.model_validate(dict(value))

    @classmethod
    def from_yaml(cls, path: str | PathLike[str]) -> "Pipeline":
        """Load a UTF-8 YAML pipeline using Provium's safe YAML loader."""
        return cls.from_mapping(load_yaml_configuration(path))


__all__ = ["Pipeline", "PipelineStep"]
