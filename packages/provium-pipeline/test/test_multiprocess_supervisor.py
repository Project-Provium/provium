from __future__ import annotations

from dataclasses import dataclass

import pytest

from provium_pipeline.multiprocessing import MultiprocessSupervisor


@dataclass
class _Process:
    alive: bool = True
    starts: int = 0
    terminates: int = 0
    joins: int = 0

    def start(self) -> None:
        self.starts += 1

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminates += 1
        self.alive = False

    def join(self) -> None:
        self.joins += 1


def test_multiprocess_supervisor_rejects_nonpositive_slots() -> None:
    with pytest.raises(ValueError, match="slots must be positive"):
        MultiprocessSupervisor(
            slots=0,
            process_factory=lambda _identity: _Process(),
            is_terminal=lambda: True,
            wait_for_change=lambda: None,
        )


def test_multiprocess_supervisor_joins_already_exited_worker_at_terminal() -> None:
    process = _Process(alive=False)
    supervisor = MultiprocessSupervisor(
        slots=1,
        process_factory=lambda _identity: process,
        is_terminal=lambda: True,
        wait_for_change=lambda: None,
    )

    supervisor.run()

    assert process.starts == 1
    assert process.terminates == 0
    assert process.joins == 1


def test_multiprocess_supervisor_cleans_up_when_polling_fails() -> None:
    process = _Process()

    def fail() -> None:
        raise RuntimeError("poll failed")

    supervisor = MultiprocessSupervisor(
        slots=1,
        process_factory=lambda _identity: process,
        is_terminal=lambda: False,
        wait_for_change=fail,
    )

    with pytest.raises(RuntimeError, match="poll failed"):
        supervisor.run()

    assert process.terminates == 1
    assert process.joins == 1


def test_multiprocess_supervisor_replaces_crashed_slot_and_stops_at_terminal() -> None:
    created: list[tuple[str, _Process]] = []
    terminal = False

    def factory(identity: str) -> _Process:
        process = _Process()
        created.append((identity, process))
        return process

    polls = 0

    def wait_for_change() -> None:
        nonlocal polls, terminal
        polls += 1
        if polls == 1:
            created[0][1].alive = False
        elif polls == 2:
            terminal = True

    supervisor = MultiprocessSupervisor(
        slots=2,
        process_factory=factory,
        is_terminal=lambda: terminal,
        wait_for_change=wait_for_change,
    )

    supervisor.run()

    assert [identity for identity, _process in created] == [
        "pipeline-worker-0",
        "pipeline-worker-1",
        "pipeline-worker-0",
    ]
    original, second, replacement = (process for _identity, process in created)
    assert original.starts == 1
    assert original.terminates == 0
    assert original.joins == 1
    assert second.terminates == 1
    assert second.joins == 1
    assert replacement.terminates == 1
    assert replacement.joins == 1
