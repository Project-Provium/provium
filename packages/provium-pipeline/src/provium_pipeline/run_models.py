from __future__ import annotations

from enum import StrEnum

from provium import canonical_digest

from .compiler import CompiledPipeline
from .inputs import RunInputSnapshot


class TaskState(StrEnum):
    """Durable lifecycle state for one planned pipeline task."""

    BLOCKED = "blocked"
    READY = "ready"
    WAITING_ON_COMPUTATION = "waiting-on-computation"
    LEASED = "leased"
    RETRY_WAIT = "retry-wait"
    SUCCEEDED = "succeeded"
    REUSED = "reused"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def satisfied(self) -> bool:
        return self in {TaskState.SUCCEEDED, TaskState.REUSED}


def run_fingerprint(
    pipeline: CompiledPipeline,
    inputs: RunInputSnapshot,
) -> str:
    """Return the stable identity of an intended run."""
    return canonical_digest(
        {
            "pipeline": pipeline.semantic_digest,
            "configuration": pipeline.resolved_configuration.document_digest,
            "inputs": inputs.digest,
        }
    )


__all__ = ["TaskState", "run_fingerprint"]
