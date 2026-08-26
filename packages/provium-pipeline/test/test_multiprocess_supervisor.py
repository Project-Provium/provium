from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from provium_pipeline.multiprocessing import (
    MultiprocessSupervisor,
    SpawnProcessFactory,
    run_spawn_worker,
    run_worker_runtime,
)


def _record_spawned_worker(identity: str, directory: str) -> None:
    Path(directory, identity).write_text(str(os.getpid()), encoding="utf-8")


@dataclass
class _OwnedRuntime:
    path: str

    def run(self) -> None:
        with Path(self.path).open("a", encoding="utf-8") as stream:
            stream.write(f"run:{os.getpid()}\n")

    def close(self) -> None:
        with Path(self.path).open("a", encoding="utf-8") as stream:
            stream.write(f"close:{os.getpid()}\n")


@dataclass(frozen=True)
class _OwnedRuntimeFactory:
    directory: str

    def __call__(self, identity: str) -> _OwnedRuntime:
        path = str(Path(self.directory, f"{identity}.runtime"))
        Path(path).write_text(f"create:{os.getpid()}\n", encoding="utf-8")
        return _OwnedRuntime(path)


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


def test_worker_runtime_is_created_run_and_closed_inside_spawned_child(
    tmp_path: Path,
) -> None:
    factory = SpawnProcessFactory(
        target=run_worker_runtime,
        args=(_OwnedRuntimeFactory(str(tmp_path)),),
    )
    process = factory("pipeline-worker-3")

    process.start()
    process.join()

    events = (
        (tmp_path / "pipeline-worker-3.runtime")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    child_pids = {event.partition(":")[2] for event in events}
    assert [event.partition(":")[0] for event in events] == ["create", "run", "close"]
    assert len(child_pids) == 1
    assert str(os.getpid()) not in child_pids


def test_worker_runtime_runs_and_closes_in_order() -> None:
    events: list[str] = []

    class _Runtime:
        def run(self) -> None:
            events.append("run")

        def close(self) -> None:
            events.append("close")

    run_worker_runtime("pipeline-worker-0", lambda _identity: _Runtime())

    assert events == ["run", "close"]


def test_worker_runtime_closes_then_reraises_run_error() -> None:
    run_error = RuntimeError("run failed")
    closed = False

    class _FailingRuntime:
        def run(self) -> None:
            raise run_error

        def close(self) -> None:
            nonlocal closed
            closed = True

    with pytest.raises(RuntimeError, match="run failed") as caught:
        run_worker_runtime("pipeline-worker-0", lambda _identity: _FailingRuntime())

    assert caught.value is run_error
    assert closed


def test_worker_runtime_preserves_run_error_when_close_also_fails() -> None:
    run_error = RuntimeError("run failed")
    close_error = OSError("close failed")

    class _FailingRuntime:
        def run(self) -> None:
            raise run_error

        def close(self) -> None:
            raise close_error

    with pytest.raises(RuntimeError, match="run failed") as caught:
        run_worker_runtime("pipeline-worker-0", lambda _identity: _FailingRuntime())

    assert caught.value is run_error
    assert caught.value.__cause__ is close_error


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


def test_multiprocess_supervisor_requests_cooperative_shutdown_before_termination() -> (
    None
):
    events: list[str] = []

    class _OrderedProcess(_Process):
        def terminate(self) -> None:
            events.append("terminate")
            super().terminate()

        def join(self) -> None:
            events.append("join")
            super().join()

    supervisor = MultiprocessSupervisor(
        slots=1,
        process_factory=lambda _identity: _OrderedProcess(),
        is_terminal=lambda: True,
        wait_for_change=lambda: None,
        request_cancellation=lambda: events.append("cancel"),
        wait_for_shutdown=lambda: events.append("grace"),
    )

    supervisor.run()

    assert events == ["cancel", "grace", "terminate", "join"]


def test_multiprocess_supervisor_preserves_poll_error_over_shutdown_failure() -> None:
    poll_error = RuntimeError("poll failed")
    shutdown_error = OSError("cancel failed")
    joined: list[None] = []

    class _JoinedProcess(_Process):
        def join(self) -> None:
            joined.append(None)
            super().join()

    def fail_poll() -> None:
        raise poll_error

    def fail_cancellation() -> None:
        raise shutdown_error

    supervisor = MultiprocessSupervisor(
        slots=1,
        process_factory=lambda _identity: _JoinedProcess(),
        is_terminal=lambda: False,
        wait_for_change=fail_poll,
        request_cancellation=fail_cancellation,
    )

    with pytest.raises(RuntimeError, match="poll failed") as caught:
        supervisor.run()

    assert caught.value is poll_error
    assert caught.value.__cause__ is shutdown_error
    assert joined == [None]


def test_multiprocess_supervisor_attempts_every_shutdown_step_after_failures() -> None:
    events: list[str] = []
    cancellation_error = RuntimeError("cancel failed")

    def failing_action(name: str, error: BaseException) -> None:
        events.append(name)
        raise error

    class _FailingShutdownProcess(_Process):
        def is_alive(self) -> bool:
            events.append("alive")
            raise OSError("alive failed")

        def terminate(self) -> None:
            events.append("terminate")
            raise OSError("terminate failed")

        def join(self) -> None:
            events.append("join")
            raise OSError("join failed")

    supervisor = MultiprocessSupervisor(
        slots=1,
        process_factory=lambda _identity: _FailingShutdownProcess(),
        is_terminal=lambda: True,
        wait_for_change=lambda: None,
        request_cancellation=lambda: failing_action("cancel", cancellation_error),
        wait_for_shutdown=lambda: failing_action("grace", OSError("grace failed")),
    )

    with pytest.raises(RuntimeError, match="cancel failed") as caught:
        supervisor.run()

    assert caught.value is cancellation_error
    assert events == ["cancel", "grace", "alive", "terminate", "join"]


def test_multiprocess_supervisor_cleans_up_partially_started_workers() -> None:
    process = _Process()
    starts = 0

    def factory(_identity: str) -> _Process:
        nonlocal starts
        starts += 1
        if starts == 2:
            raise RuntimeError("spawn failed")
        return process

    supervisor = MultiprocessSupervisor(
        slots=2,
        process_factory=factory,
        is_terminal=lambda: False,
        wait_for_change=lambda: None,
    )

    with pytest.raises(RuntimeError, match="spawn failed"):
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
