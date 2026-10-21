"""Backend-independent persistence contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..models import PipelineDefinition


@dataclass(frozen=True)
class Lease:
    namespace: str
    key: str
    owner: str
    generation: int
    run: str


class QueueBackend(Protocol):
    """Storage-independent coordination contract used by the execution context."""

    def definition(self, pipeline: str) -> PipelineDefinition | None: ...

    def pending(
        self,
        pipeline: str,
        after: int = 0,
        limit: int = 100,
        include_finished: bool = False,
    ) -> list[dict[str, Any]]: ...

    def progress(self, identity: int, state: str, detail: dict[str, Any]) -> None: ...

    def result(self, namespace: str, key: str) -> dict[str, Any] | None: ...

    def acquire(
        self,
        namespace: str,
        key: str,
        run: str,
        *,
        force: bool = False,
        ttl: float = 60,
        input_id: int | None = None,
    ) -> Lease | None: ...

    def renew(self, lease: Lease, *, ttl: float = 60) -> bool: ...

    def complete(self, lease: Lease, outputs: dict[str, Any]) -> bool: ...

    def fail(self, lease: Lease, error: str) -> None: ...


class PersistenceBackend(QueueBackend, Protocol):
    """Registry and input management in addition to execution coordination."""

    def register(
        self,
        documents: list[dict[str, Any]],
        *,
        definitions: dict[str, PipelineDefinition] | None = None,
    ) -> None: ...

    def registered(self, kind: str | None = None) -> list[dict[str, Any]]: ...

    def queue_info(self, pipeline: str | None = None) -> list[dict[str, Any]]: ...

    def unregister(self, documents: list[dict[str, Any]]) -> dict[str, int]: ...

    def get(self, kind: str, name: str) -> dict[str, Any]: ...

    def enqueue(self, pipeline: str, inputs: dict[str, Any]) -> int: ...

    def retry(self, pipeline: str) -> int: ...
