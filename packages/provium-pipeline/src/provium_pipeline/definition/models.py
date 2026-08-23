"""Primitive models shared by pipeline definitions."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PipelineInputScope(StrEnum):
    """Whether an input is bound per record or once per run."""

    RECORD = "record"
    SHARED = "shared"


class PipelineInputCardinality(BaseModel):
    """Allowed number of artifact bindings for a pipeline input."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    minimum: int = Field(default=1, ge=0)
    maximum: int | None = 1

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("maximum must be at least minimum")
        return self

    @property
    def required(self) -> bool:
        return self.minimum > 0

    @property
    def repeated(self) -> bool:
        return self.maximum is None or self.maximum > 1


__all__ = ["PipelineInputCardinality", "PipelineInputScope"]
