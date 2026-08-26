"""Spawn-process lifecycle supervision for local execution."""

from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import get_context
from typing import Protocol, cast


def _noop() -> None:
    return None


class WorkerProcess(Protocol):
    """Minimal child-process surface required by the supervisor."""

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    def join(self) -> None: ...


class WorkerRuntime(Protocol):
    """Child-owned runtime for one local worker process."""

    def run(self) -> None: ...

    def close(self) -> None: ...


def run_worker_runtime(
    identity: str,
    runtime_factory: Callable[[str], WorkerRuntime],
) -> None:
    """Construct, run, and close all worker-owned state inside the child."""
    runtime = runtime_factory(identity)
    try:
        runtime.run()
    except BaseException as error:
        try:
            runtime.close()
        except BaseException as cleanup_error:
            raise error from cleanup_error
        raise
    runtime.close()


def run_spawn_worker(
    target: Callable[..., None],
    identity: str,
    args: tuple[object, ...],
) -> None:
    target(identity, *args)


@dataclass(frozen=True, slots=True)
class SpawnProcessFactory:
    """Create named children through Python's spawn multiprocessing context."""

    target: Callable[..., None]
    args: tuple[object, ...] = ()

    def __call__(self, identity: str) -> WorkerProcess:
        process = get_context("spawn").Process(
            name=identity,
            target=run_spawn_worker,
            args=(self.target, identity, self.args),
        )
        return cast(WorkerProcess, process)


class MultiprocessSupervisor:
    """Maintain one child process per stable local worker slot."""

    def __init__(
        self,
        *,
        slots: int,
        process_factory: Callable[[str], WorkerProcess],
        is_terminal: Callable[[], bool],
        wait_for_change: Callable[[], None],
        request_cancellation: Callable[[], None] | None = None,
        wait_for_shutdown: Callable[[], None] | None = None,
    ) -> None:
        if slots <= 0:
            raise ValueError("slots must be positive")
        self._slots = slots
        self._process_factory = process_factory
        self._is_terminal = is_terminal
        self._wait_for_change = wait_for_change
        self._request_cancellation = request_cancellation or _noop
        self._wait_for_shutdown = wait_for_shutdown or _noop

    def _start(self, slot: int) -> WorkerProcess:
        process = self._process_factory(f"pipeline-worker-{slot}")
        process.start()
        return process

    def _shutdown(self, processes: dict[int, WorkerProcess]) -> None:
        failures: list[BaseException] = []
        for action in (self._request_cancellation, self._wait_for_shutdown):
            try:
                action()
            except BaseException as error:
                failures.append(error)
        for process in processes.values():
            try:
                alive = process.is_alive()
            except BaseException as error:
                alive = True
                failures.append(error)
            if alive:
                try:
                    process.terminate()
                except BaseException as error:
                    failures.append(error)
            try:
                process.join()
            except BaseException as error:
                failures.append(error)
        if failures:
            raise failures[0]

    def run(self) -> None:
        """Supervise workers until the dispatch reaches a terminal state."""
        processes: dict[int, WorkerProcess] = {}
        try:
            for slot in range(self._slots):
                processes[slot] = self._start(slot)
            while not self._is_terminal():
                self._wait_for_change()
                if self._is_terminal():
                    break
                for slot, process in tuple(processes.items()):
                    if process.is_alive():
                        continue
                    process.join()
                    processes[slot] = self._start(slot)
        except BaseException as error:
            try:
                self._shutdown(processes)
            except BaseException as cleanup_error:
                raise error from cleanup_error
            raise
        self._shutdown(processes)


__all__ = [
    "MultiprocessSupervisor",
    "SpawnProcessFactory",
    "WorkerProcess",
    "WorkerRuntime",
    "run_worker_runtime",
]
