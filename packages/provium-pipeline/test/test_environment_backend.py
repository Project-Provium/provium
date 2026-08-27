# pyright: reportPrivateUsage=false
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from pytest import MonkeyPatch

import provium_pipeline.artifact.service as artifact_service
import provium_pipeline.cli as cli
import provium_pipeline.dispatch_worker as dispatch_worker
import provium_pipeline.local_task_attempt as local_task_attempt
import provium_pipeline.task_executor as task_executor


def test_environment_backend_wires_run_executor(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    runtime: dict[str, object] = {}
    closed = False
    task_executed = False

    class _Cache:
        def close(self) -> None:
            nonlocal closed
            closed = True

    class _ImportService:
        def __init__(self, **kwargs: object) -> None:
            pass

        def import_artifact(self, path: Path) -> object:
            return type("_Descriptor", (), {"identity": str(path)})()

    class _TaskExecutor:
        def __init__(self, **kwargs: object) -> None:
            runtime["importer"] = kwargs["importer"]

        def execute(self, task: object, lease: object) -> None:
            nonlocal task_executed
            task_executed = True

    class _Worker:
        def __init__(self, **kwargs: object) -> None:
            runtime.update(kwargs)

        def run(self, dispatch: object) -> None:
            pass

    class _Backend:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured.update(kwargs)

        def execute(
            self,
            group: str,
            action: str,
            arguments: argparse.Namespace,
        ) -> cli.CLIResult:
            return cli.CLIResult({"group": group, "action": action})

    monkeypatch.setenv(
        "PROVIUM_PIPELINE_DATABASE",
        str(tmp_path / "pipeline.sqlite3"),
    )
    monkeypatch.setattr(cli, "LocalCLIBackend", _Backend)
    monkeypatch.setattr(artifact_service, "ArtifactImportService", _ImportService)
    monkeypatch.setattr(dispatch_worker, "SerialDispatchWorker", _Worker)
    monkeypatch.setattr(local_task_attempt, "LocalTaskAttemptExecutor", _TaskExecutor)
    monkeypatch.setattr(task_executor, "PreparedProcedureCache", _Cache)

    result = cli._EnvironmentBackend().execute(
        "run",
        "execute",
        argparse.Namespace(),
    )

    assert isinstance(result, cli.CLIResult)
    run_executor = cast(Any, captured["run_executor"])
    resolved_run_creator = cast(Any, captured["resolved_run_creator"])
    assert run_executor._runs is not None
    assert resolved_run_creator._runs is not None
    assert closed

    clock = runtime["clock"]
    execute_task = runtime["execute_task"]
    importer = cast(Any, runtime["importer"])
    assert callable(clock)
    assert callable(execute_task)
    clock()
    execute_task(object(), object())
    assert task_executed
    assert importer.import_artifact(Path("result.txt")) == "result.txt"
