"""CLI integration tests for execution errors and rollback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support.provium_test_pipeline.artifacts import (
    TextReader,
    TextWriter,
)

from provium import Artifact, ArtifactDefinition, ImperativeProcedure
from provium.cli import run
from provium.cli.commands import catalog as command_catalog

OTHER_ARTIFACT = ArtifactDefinition(
    "test.OtherTextV1",
    f"{__name__}:OtherArtifact",
    "A deliberately incompatible integration-test artifact.",
)


class OtherArtifact(Artifact[TextReader, TextWriter]):
    definition = OTHER_ARTIFACT
    reader = TextReader
    writer = TextWriter


def invoke(
    arguments: list[str],
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str, str]:
    status = run(arguments, catalog=command_catalog)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def create_source(
    tmp_path: Path,
    name: str,
    capsys: pytest.CaptureFixture[str],
) -> Path:
    configuration = tmp_path / f"{name}.json"
    destination = tmp_path / f"{name}.provium"
    configuration.write_text(json.dumps({"text": name}), encoding="utf-8")
    status, _, stderr = invoke(
        [
            "execute",
            "test.SourceTextV1",
            "--procedure-config",
            str(configuration),
            "--output",
            f"value={destination}",
        ],
        capsys,
    )
    assert status == 0
    assert stderr == ""
    return destination


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["execute", "test.MissingV1"], "unknown procedure"),
        (
            [
                "execute",
                "test.TransformTextV1",
                "--procedure-config",
                "configuration.txt",
            ],
            "unsupported configuration file type",
        ),
        (
            ["execute", "test.TransformTextV1", "--input", "invalid"],
            "binding must use FIELD=PATH syntax",
        ),
        (
            ["execute", "test.TransformTextV1", "--input", "unknown=value"],
            "unknown binding field: unknown",
        ),
        (
            ["execute", "test.SourceTextV1", "--procedure-config", "missing.json"],
            "No such file or directory",
        ),
        (
            [
                "execute",
                "test.SourceTextV1",
                "--output",
                "value=first.provium",
                "--output",
                "value=second.provium",
            ],
            "binding field value may be supplied only once",
        ),
    ],
)
def test_basic_cli_errors_have_status_two_and_stderr_only(
    discovered_pipeline: Path,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    message: str,
) -> None:
    del discovered_pipeline
    status, stdout, stderr = invoke(arguments, capsys)

    assert status == 2
    assert stdout == ""
    assert stderr.startswith("error: ")
    assert message in stderr


def test_missing_required_binding_and_invalid_configuration_are_reported(
    discovered_pipeline: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del discovered_pipeline
    configuration = tmp_path / "source.json"
    configuration.write_text(json.dumps({"text": "value"}), encoding="utf-8")
    status, stdout, stderr = invoke(
        ["execute", "test.SourceTextV1", "--procedure-config", str(configuration)],
        capsys,
    )
    assert status == 2
    assert stdout == ""
    assert "missing required field: value" in stderr

    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"prefix": 1}), encoding="utf-8")
    status, stdout, stderr = invoke(
        ["execute", "test.TransformTextV1", "--procedure-config", str(invalid)],
        capsys,
    )
    assert status == 2
    assert stdout == ""
    assert "invalid configuration" in stderr


@pytest.mark.parametrize(
    ("repeated_count", "message"),
    [(0, "requires at least 1 binding"), (5, "permits at most 4 bindings")],
)
def test_repeated_binding_cardinality_errors_are_reported(
    discovered_pipeline: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    repeated_count: int,
    message: str,
) -> None:
    del discovered_pipeline
    setup = create_source(tmp_path, "setup", capsys)
    required = create_source(tmp_path, "required", capsys)
    repeated = create_source(tmp_path, "repeated", capsys)
    repeated_arguments = [
        argument
        for _ in range(repeated_count)
        for argument in ("--input", f"repeated={repeated}")
    ]

    status, stdout, stderr = invoke(
        [
            "execute",
            "test.TransformTextV1",
            "--setup-input",
            f"setup={setup}",
            "--input",
            f"required={required}",
            *repeated_arguments,
            "--output",
            f"transformed={tmp_path / 'transformed.provium'}",
            "--output",
            f"summary={tmp_path / 'summary.provium'}",
        ],
        capsys,
    )

    assert status == 2
    assert stdout == ""
    assert message in stderr


def test_artifact_type_mismatch_is_reported(
    discovered_pipeline: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del discovered_pipeline
    setup = create_source(tmp_path, "setup", capsys)
    repeated = create_source(tmp_path, "repeated", capsys)
    incompatible = tmp_path / "incompatible.provium"
    binding = OtherArtifact.bind_write(incompatible)
    with ImperativeProcedure("test.OtherSourceV1", "digest").execute(
        outputs={"value": binding}
    ):
        with binding.open() as writer:
            writer.write_text("other")

    status, stdout, stderr = invoke(
        [
            "execute",
            "test.TransformTextV1",
            "--setup-input",
            f"setup={setup}",
            "--input",
            f"required={incompatible}",
            "--input",
            f"repeated={repeated}",
            "--output",
            f"transformed={tmp_path / 'transformed.provium'}",
            "--output",
            f"summary={tmp_path / 'summary.provium'}",
        ],
        capsys,
    )

    assert status == 2
    assert stdout == ""
    assert "artifact does not match the requested artifact type" in stderr


def test_processing_failure_preserves_existing_destination(
    discovered_pipeline: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del discovered_pipeline
    source = create_source(tmp_path, "source", capsys)
    destination = tmp_path / "destination.provium"
    destination.write_bytes(b"original")

    status, stdout, stderr = invoke(
        [
            "execute",
            "test.FailingTextV1",
            "--input",
            f"source={source}",
            "--output",
            f"result={destination}",
        ],
        capsys,
    )

    assert status == 2
    assert stdout == ""
    assert "deliberate integration test failure" in stderr
    assert destination.read_bytes() == b"original"
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.backup"))
