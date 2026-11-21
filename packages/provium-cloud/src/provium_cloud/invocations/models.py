"""Serializable plans, independent of scheduling providers and run identity."""

from typing import Any, Literal

from provium_pipeline.models import Node, PipelineConfiguration
from pydantic import BaseModel, ConfigDict, Field

from provium_cloud.deployment import JobSettings


class PipelineGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["PipelineConfiguration"] = "PipelineConfiguration"
    inputs: dict[str, str | dict[str, str]]
    nodes: dict[str, Node]
    targets: dict[str, str]

    def pipeline(self) -> PipelineConfiguration:
        # The legacy planner requires a name; it never enters cloud identity or state.
        return PipelineConfiguration.model_validate(
            {**self.model_dump(), "name": "cloud"}
        )


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    procedure: str
    record: dict[str, Any]
    configuration: dict[str, Any]
    inputs: dict[str, str | None]
    setup_inputs: dict[str, str | None]
    value_types: dict[str, str] = Field(default_factory=dict)
    output_types: dict[str, str]
    settings: JobSettings
    force: bool = False


class InvocationPlan(BaseModel):
    graph: PipelineGraph
    tasks: dict[str, TaskPlan]
    targets: dict[str, str]
