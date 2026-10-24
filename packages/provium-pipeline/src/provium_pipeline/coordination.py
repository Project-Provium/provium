"""Storage-independent computation admission and commit fencing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Lease:
    namespace: str
    key: str
    owner: str
    generation: int
    run: str


class WorkQueue(Protocol):
    """Cursor-paged requests and durable outcome reporting for schedulers."""

    def pending(
        self,
        pipeline: str,
        after: int = 0,
        limit: int = 100,
        include_finished: bool = False,
        *,
        queue_group: str = "default",
    ) -> list[dict[str, Any]]: ...

    def progress(self, identity: int, state: str, detail: dict[str, Any]) -> None: ...


class Coordinator(Protocol):
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
