"""Artifact retention reference accounting."""

from dataclasses import dataclass
from enum import StrEnum


class RetentionClass(StrEnum):
    """Policy class controlling how strongly an artifact is retained."""

    PINNED = "pinned"
    RUN = "run"
    CACHE = "cache"
    EPHEMERAL = "ephemeral"


class RetentionReferenceSource(StrEnum):
    """Pipeline-owned domains that can retain a logical artifact."""

    RUN_INPUT = "run-input"
    RUN_OUTPUT = "run-output"
    TASK_OUTPUT = "task-output"
    COMPUTATION_CACHE = "computation-cache"
    INPUT_SET = "input-set"
    USER_PIN = "user-pin"
    ACTIVE_ATTEMPT = "active-attempt"


@dataclass(frozen=True, slots=True)
class RetentionReference:
    """One durable ownership edge from a domain object to an artifact."""

    artifact_identity: str
    source: RetentionReferenceSource
    owner_identity: str
    retention_class: RetentionClass


class InMemoryRetentionIndex:
    """Deterministic reference index used by local services and conformance tests."""

    def __init__(self) -> None:
        self._references: set[RetentionReference] = set()

    def add(self, reference: RetentionReference) -> None:
        """Register a reference idempotently."""
        self._references.add(reference)

    def remove(self, reference: RetentionReference) -> None:
        """Remove a reference idempotently."""
        self._references.discard(reference)

    def list_for_artifact(
        self, artifact_identity: str
    ) -> tuple[RetentionReference, ...]:
        """List an artifact's references in stable source/owner/class order."""
        references = (
            reference
            for reference in self._references
            if reference.artifact_identity == artifact_identity
        )
        return tuple(
            sorted(
                references,
                key=lambda reference: (
                    reference.source.value,
                    reference.owner_identity,
                    reference.retention_class.value,
                ),
            )
        )

    def is_retained(self, artifact_identity: str) -> bool:
        """Return whether any active ownership edge retains the artifact."""
        return any(
            reference.artifact_identity == artifact_identity
            for reference in self._references
        )


__all__ = [
    "InMemoryRetentionIndex",
    "RetentionClass",
    "RetentionReference",
    "RetentionReferenceSource",
]
