"""Durable runs, task membership, and shared computation generations."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from provium_cloud.artifacts import ArtifactReference
from provium_cloud.deployment import CloudDeployment, JobSettings
from provium_cloud.invocations.models import TaskPlan

RunStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]
TaskStatus = Literal[
    "waiting", "queued", "running", "completed", "failed", "blocked", "canceled"
]
GenerationStatus = Literal[
    "queued",
    "submitting",
    "submitted",
    "running",
    "succeeded",
    "failed",
    "canceled",
    "superseded",
]


class Task(BaseModel):
    plan: TaskPlan
    status: TaskStatus = "waiting"
    computation: str | None = None
    generation: int | None = None
    reused: bool = False
    outputs: dict[str, ArtifactReference] = Field(default_factory=dict)
    error: str | None = None


class Run(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    input_set_id: str
    inputs: dict[str, Any]
    targets: dict[str, str]
    tasks: dict[str, Task]
    deployment: CloudDeployment
    status: RunStatus = "queued"
    created_at: float
    updated_at: float
    outputs: dict[str, ArtifactReference] = Field(default_factory=dict)
    error: str | None = None

    @property
    def progress(self) -> dict[str, int]:
        counts = {
            state: 0
            for state in (
                "waiting",
                "queued",
                "running",
                "completed",
                "failed",
                "blocked",
                "canceled",
            )
        }
        for task in self.tasks.values():
            counts[task.status] += 1
        return {
            "total": len(self.tasks),
            **counts,
            "reused": sum(
                task.reused
                for task in self.tasks.values()
                if task.status == "completed"
            ),
        }

    def raise_for_failure(self) -> None:
        if self.status != "succeeded":
            raise RuntimeError(self.error or f"run {self.id} is {self.status}")


class Generation(BaseModel):
    number: int
    attempt: str
    payload: dict[str, Any]
    settings: JobSettings
    forced: bool = False
    status: GenerationStatus = "queued"
    job_ids: list[str] = Field(default_factory=list[str])
    owner: str | None = None
    lease_until: float = 0
    submit_owner: str | None = None
    submit_until: float = 0
    cancellation_pending: bool = False
    error: str | None = None
    outputs: dict[str, ArtifactReference] | None = None
    created_at: float
    updated_at: float


class Computation(BaseModel):
    key: str
    generations: list[Generation] = Field(default_factory=list[Generation])


class RunPage(BaseModel):
    items: list[Run]
    next_cursor: str | None
