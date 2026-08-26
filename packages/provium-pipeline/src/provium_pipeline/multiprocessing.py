"""Spawn-process lifecycle supervision for local execution."""

from collections.abc import Callable
from typing import Protocol


class WorkerProcess(Protocol):
    """Minimal child-process surface required by the supervisor."""

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    def join(self) -> None: ...


class MultiprocessSupervisor:
    """Maintain one child process per stable local worker slot."""

    def __init__(
        self,
        *,
        slots: int,
        process_factory: Callable[[str], WorkerProcess],
        is_terminal: Callable[[], bool],
        wait_for_change: Callable[[], None],
    ) -> None:
        if slots <= 0:
            raise ValueError("slots must be positive")
        self._slots = slots
        self._process_factory = process_factory
        self._is_terminal = is_terminal
        self._wait_for_change = wait_for_change

    def _start(self, slot: int) -> WorkerProcess:
        process = self._process_factory(f"pipeline-worker-{slot}")
        process.start()
        return process

    def run(self) -> None:
        """Supervise workers until the dispatch reaches a terminal state."""
        processes = {slot: self._start(slot) for slot in range(self._slots)}
        try:
            while not self._is_terminal():
                self._wait_for_change()
                if self._is_terminal():
                    break
                for slot, process in tuple(processes.items()):
                    if process.is_alive():
                        continue
                    process.join()
                    processes[slot] = self._start(slot)
        finally:
            for process in processes.values():
                if process.is_alive():
                    process.terminate()
                process.join()


__all__ = ["MultiprocessSupervisor", "WorkerProcess"]
