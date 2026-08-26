from collections.abc import Callable

from provium_pipeline.serial import SerialRunner


def test_serial_runner_claims_executes_and_waits_when_idle() -> None:
    claims = iter((None, "task-1", "task-2"))
    executed: list[str] = []
    waits: list[None] = []

    runner = SerialRunner(
        claim=lambda: next(claims),
        execute=executed.append,
        is_terminal=lambda: len(executed) == 2,
        wait_for_work=lambda: waits.append(None),
    )

    runner.run()

    assert executed == ["task-1", "task-2"]
    assert waits == [None]


def test_serial_runner_does_nothing_for_terminal_dispatch() -> None:
    called: list[str] = []

    def record(name: str) -> Callable[[], None]:
        return lambda: called.append(name)

    runner: SerialRunner[str] = SerialRunner(
        claim=lambda: called.append("claim") or None,
        execute=lambda _lease: called.append("execute"),
        is_terminal=lambda: True,
        wait_for_work=record("wait"),
    )

    runner.run()

    assert called == []
