from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol

from provium_pipeline.dispatch.models import Dispatch, DispatchState
from provium_pipeline.dispatch.transitions import apply_dispatch_transition
from provium_pipeline.identifiers import DispatchId, RunId


class DispatchRedefinitionError(ValueError):
    """Raised when one dispatch ID is reused for different content."""


class DispatchStore(Protocol):
    """Persistence contract for immutable dispatch snapshots."""

    def create(self, dispatch: Dispatch) -> Dispatch: ...

    def get(self, identifier: DispatchId) -> Dispatch: ...

    def list_for_run(self, run_identifier: RunId) -> tuple[Dispatch, ...]: ...

    def transition(
        self,
        identifier: DispatchId,
        *,
        expected: DispatchState,
        target: DispatchState,
    ) -> Dispatch: ...

    def cancel(self, identifier: DispatchId) -> Dispatch: ...


class InMemoryDispatchStore:
    """Thread-safe deterministic reference dispatch store."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = RLock()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._dispatches: dict[DispatchId, Dispatch] = {}

    def create(self, dispatch: Dispatch) -> Dispatch:
        with self._lock:
            existing = self._dispatches.get(dispatch.identifier)
            if existing is not None:
                if existing != dispatch:
                    raise DispatchRedefinitionError(
                        f"dispatch identifier already identifies other content: "
                        f"{dispatch.identifier}"
                    )
                return existing
            self._dispatches[dispatch.identifier] = dispatch
            return dispatch

    def get(self, identifier: DispatchId) -> Dispatch:
        with self._lock:
            try:
                return self._dispatches[identifier]
            except KeyError:
                raise KeyError(f"unknown dispatch identifier: {identifier}") from None

    def list_for_run(self, run_identifier: RunId) -> tuple[Dispatch, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        dispatch
                        for dispatch in self._dispatches.values()
                        if dispatch.run_identifier == run_identifier
                    ),
                    key=lambda dispatch: str(dispatch.identifier),
                )
            )

    def transition(
        self,
        identifier: DispatchId,
        *,
        expected: DispatchState,
        target: DispatchState,
    ) -> Dispatch:
        with self._lock:
            transitioned = apply_dispatch_transition(
                self.get(identifier),
                expected=expected,
                target=target,
                transitioned_at=self._clock(),
            )
            self._dispatches[identifier] = transitioned
            return transitioned

    def cancel(self, identifier: DispatchId) -> Dispatch:
        with self._lock:
            dispatch = self.get(identifier)
            if dispatch.state is DispatchState.CANCELLED:
                return dispatch
            cancelled = apply_dispatch_transition(
                dispatch,
                expected=dispatch.state,
                target=DispatchState.CANCELLED,
                transitioned_at=self._clock(),
            )
            self._dispatches[identifier] = cancelled
            return cancelled


__all__ = [
    "DispatchRedefinitionError",
    "DispatchStore",
    "InMemoryDispatchStore",
]
