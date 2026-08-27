from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, cast

import pytest


class _ExecResult(Protocol):
    exit_code: int
    output: bytes | str


class _Container(Protocol):
    def with_volume_mapping(
        self,
        host: str,
        container: str,
        mode: str = "rw",
    ) -> _Container: ...

    def with_command(self, command: str) -> _Container: ...

    def exec(self, command: list[str]) -> _ExecResult: ...

    def __enter__(self) -> _Container: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


DockerContainer = cast(
    Callable[[str], _Container],
    getattr(import_module("testcontainers.core.container"), "DockerContainer"),
)


@pytest.mark.integration
@pytest.mark.testcontainers
def test_installed_wheels_execute_local_cli_workflow(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[4]
    wheels = tmp_path / "wheels"
    workspace = tmp_path / "workspace"
    wheels.mkdir()
    workspace.mkdir()
    _build_wheel(repository, repository / "packages" / "provium", wheels)
    _build_wheel(
        repository,
        repository / "packages" / "provium-pipeline",
        wheels,
    )
    _build_wheel(
        repository,
        repository / "examples" / "provium-text-pipeline",
        wheels,
    )
    (workspace / "source.txt").write_text("Hello, container world!\n", encoding="utf-8")
    (workspace / "setup_workflow.py").write_text(
        _WORKFLOW_SETUP,
        encoding="utf-8",
    )

    container = (
        DockerContainer("python:3.12-slim")
        .with_volume_mapping(str(wheels), "/wheels", mode="ro")
        .with_volume_mapping(str(workspace), "/workspace", mode="rw")
        .with_command("sleep infinity")
    )
    with container:
        _exec(
            container, "python -m pip install --disable-pip-version-check /wheels/*.whl"
        )
        help_output = _exec(container, "provium pipeline --help")
        assert "{validate,show,execute,enqueue,list}" in help_output
        _exec(
            container,
            "provium artifact load provium_text_pipeline_example.DocumentV1 "
            "source.txt document.pa",
        )
        _exec(container, "python setup_workflow.py")

        created_input_set = _json_exec(
            container,
            "provium input-set create /workspace/inputs.ndjson "
            "--identifier acceptance-inputs --format json",
        )
        assert created_input_set["data"]["identifier"] == "acceptance-inputs"

        created_run = _json_exec(
            container,
            "provium run create /workspace/pipeline.json "
            "--input-set acceptance-inputs --format json",
        )
        run_id = created_run["data"]["run_id"]
        planned_tasks = _json_exec(
            container,
            f"provium run tasks {run_id} --format json",
        )
        assert len(planned_tasks["data"]["tasks"]) == 1

        executed = _json_exec(
            container,
            f"provium run execute {run_id} --all --format json",
        )
        assert executed["data"]["dispatch"]["state"] == "succeeded"

        status = _json_exec(
            container,
            f"provium run status {run_id} --format json",
        )
        assert status["data"]["status"] == "succeeded"

        outputs = _json_exec(
            container,
            f"provium run outputs {run_id} --format json",
        )
        assert outputs["data"]["produced"]

        artifacts = _json_exec(
            container,
            f"provium run artifacts {run_id} --locations --format json",
        )
        assert len(artifacts["data"]["artifacts"]) == 1
        assert artifacts["data"]["artifacts"][0]["locations"]


def _build_wheel(repository: Path, package: Path, destination: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(destination),
            str(package),
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def _json_exec(container: _Container, command: str) -> dict[str, Any]:
    return json.loads(_exec(container, command))


_WORKFLOW_SETUP = """\
import json
from pathlib import Path

from provium_pipeline.artifact.filesystem import FilesystemArtifactStore
from provium_pipeline.artifact.service import ArtifactImportService
from provium_pipeline.artifact.sqlite_index import SQLiteArtifactIndex
from provium_pipeline.definition.builder import PipelineBuilder
from provium_pipeline.definition.codec import canonical_definition_document
from provium_text_pipeline_example.artifact import catalog as artifact_catalog
from provium_text_pipeline_example.procedure import catalog as procedure_catalog

DOCUMENT = artifact_catalog.definitions["provium_text_pipeline_example.DocumentV1"]
TOKENIZE = procedure_catalog.definitions["provium_text_pipeline_example.TokenizeV1"]
builder = PipelineBuilder(identifier="acceptance.pipeline", version="1")
source = builder.record_input("source", DOCUMENT)
node = builder.node("tokenize", TOKENIZE, inputs={"source": source})
builder.output("tokens", node.output("destination"))
definition = builder.build()
Path("pipeline.json").write_text(
    json.dumps(canonical_definition_document(definition)),
    encoding="utf-8",
)

database = Path(".provium/pipeline.sqlite3")
database.parent.mkdir(parents=True, exist_ok=True)
store = FilesystemArtifactStore(identifier="local", root=database.parent / "artifacts")
index = SQLiteArtifactIndex(database)
descriptor = ArtifactImportService(store=store, index=index).import_artifact(
    Path("document.pa")
)
Path("inputs.ndjson").write_text(
    json.dumps(
        {
            "key": "record-1",
            "inputs": {"source": [descriptor.identity]},
            "labels": {},
        }
    )
    + "\\n",
    encoding="utf-8",
)
"""


def _exec(container: _Container, command: str) -> str:
    result = container.exec(["sh", "-lc", f"cd /workspace && {command}"])
    output = (
        result.output.decode() if isinstance(result.output, bytes) else result.output
    )
    assert result.exit_code == 0, output
    return output
