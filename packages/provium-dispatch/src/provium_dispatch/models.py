"""Named execution profiles and durable definition constraints."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from provium_pipeline.models import Document, ExecutionSettings
from provium_pipeline.models import PipelineDefinition as BasicDefinition


def definition_snapshot(definition: BasicDefinition) -> dict[str, Any]:
    """Normalize a basic definition to the durable snapshot schema."""
    snapshot = definition.snapshot()
    snapshot.setdefault("profiles", [])
    return snapshot


class ComputeProfile(ExecutionSettings, Document):
    kind: Literal["ComputeProfile"] = "ComputeProfile"


@dataclass(frozen=True)
class PipelineDefinition(BasicDefinition):
    profiles: frozenset[str] = field(default_factory=frozenset[str])

    def snapshot(self) -> dict[str, Any]:
        return {**super().snapshot(), "profiles": sorted(self.profiles)}

    @classmethod
    def from_snapshot(cls, value: dict[str, Any]) -> PipelineDefinition:
        return cls(
            dict(value["inputs"]),
            dict(value["outputs"]),
            frozenset(value.get("profiles", [])),
        )
