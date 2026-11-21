"""Backend contracts for atomic scheduling decisions and durable history."""

from contextlib import AbstractContextManager
from typing import Protocol

from provium_cloud.state.models import Computation, Run


class StateTransaction(Protocol):
    now: float

    def get_run(self, identity: str) -> Run | None: ...
    def save_run(self, run: Run) -> None: ...
    def get_computation(self, key: str) -> Computation | None: ...
    def save_computation(self, computation: Computation) -> None: ...
    def runs(
        self,
        *,
        statuses: list[str] | None = None,
        input_set_id: str | None = None,
        after: str = "",
        limit: int = 100,
    ) -> list[Run]: ...
    def computations(
        self, *, active: bool = True, after: str = "", limit: int = 100
    ) -> list[Computation]: ...
    def request(self, identity: str) -> tuple[str, str] | None: ...
    def save_request(self, identity: str, run: str, digest: str) -> None: ...


class StateBackend(Protocol):
    namespace: str

    def transaction(self) -> AbstractContextManager[StateTransaction]:
        """Atomic, isolated, serializable decisions; rollback on exception.

        No external I/O may occur while holding a transaction. All callers sharing
        a namespace must observe one serial order, including absent-key reads.
        """
        ...
