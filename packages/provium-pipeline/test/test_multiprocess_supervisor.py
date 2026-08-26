from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from provium_pipeline.multiprocessing import (
    MultiprocessSupervisor,
    SpawnProcessFactory,
    run_spawn_worker,
)


def _record_spawned_worker(identity: str, directory: str) -> None:
    Path(directory, identity).write_text(str(os.getpid()), encoding="utf-8")


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


def test_spawn_worker_entry_injects_identity_before_factory_arguments() -> None:
    received: list[tuple[str, object]] = []

    def target(identity: str, value: object) -> None:
        received.append((identity, value))

    marker = object()
    run_spawn_worker(target, "pipeline-worker-2", (marker,))

    assert received == [("pipeline-worker-2", marker)]


def test_spawn_process_factory_runs_named_workers_in_distinct_children(
    tmp_path: Path,
) -> None:
    factory = SpawnProcessFactory(
        target=_record_spawned_worker,
        args=(str(tmp_path),),
    )
    processes = [factory(f"pipeline-worker-{slot}") for slot in range(4)]

    for process in processes:
        process.start()
    for process in processes:
        process.join()

    pids = {
        (tmp_path / f"pipeline-worker-{slot}").read_text(encoding="utf-8")
        for slot in range(4)
    }
    assert len(pids) == 4
    assert str(os.getpid()) not in pids


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
