"""Backend-independent persistence contracts."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Protocol

from provium_pipeline.cache import MaintenanceGuard
from provium_pipeline.coordination import Coordinator, WorkQueue
from provium_pipeline.coordination import Lease as Lease

if TYPE_CHECKING:
    from provium_pipeline.models import PipelineDefinition


class QueueBackend(Coordinator, WorkQueue, Protocol):
    """Storage-independent coordination contract used by the execution context."""

    def reader(self, namespace: str) -> AbstractContextManager[None]: ...

    def definition(self, pipeline: str) -> PipelineDefinition | None: ...


class PersistenceBackend(QueueBackend, MaintenanceGuard, Protocol):
    """Registry and input management in addition to execution coordination."""

    def register(
        self,
        documents: list[dict[str, Any]],
        *,
        definitions: dict[str, PipelineDefinition] | None = None,
    ) -> None: ...

    def register_input_set(
        self, name: str, inputs: dict[str, Any]
    ) -> dict[str, Any]: ...

    def resolve_input_set(
        self, *, name: str | None = None, key: str | None = None
    ) -> dict[str, Any]: ...

    def find_input_sets(self, key: str) -> list[str]: ...

    def unregister_input_sets(
        self, *, name: str | None = None, key: str | None = None
    ) -> list[str]: ...

    def registered(self, kind: str | None = None) -> list[dict[str, Any]]: ...

    def queue_info(
        self, pipeline: str | None = None, *, queue_group: str = "default"
    ) -> list[dict[str, Any]]: ...

    def unregister(self, documents: list[dict[str, Any]]) -> dict[str, int]: ...

    def get(self, kind: str, name: str) -> dict[str, Any]: ...

    def enqueue(
        self,
        pipeline: str,
        inputs: dict[str, Any],
        *,
        queue_group: str = "default",
        destinations: dict[str, str] | None = None,
    ) -> int: ...

    def retry(self, pipeline: str, *, queue_group: str = "default") -> int: ...
