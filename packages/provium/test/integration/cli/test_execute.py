"""CLI integration tests for real procedure execution."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from support.provium_test_pipeline.artifacts import TextArtifact

from provium import ArtifactReference, session
from provium.cli import run
from provium.cli.commands import catalog as command_catalog


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
    text: str,
    capsys: pytest.CaptureFixture[str],
) -> tuple[Path, ArtifactReference]:
    configuration = tmp_path / f"{name}.json"
    destination = tmp_path / f"{name}.provium"
    configuration.write_text(json.dumps({"text": text}), encoding="utf-8")
    status, stdout, stderr = invoke(
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
    assert str(UUID(stdout.strip())) == stdout.strip()
    assert stderr == ""
    with session():
        with TextArtifact.bind_read(destination).open() as reader:
            reference = ArtifactReference(reader.identity, reader.artifact_identifier)
    return destination, reference


def read_output(path: Path):
    with session():
        with TextArtifact.bind_read(path).open() as reader:
            return reader.read_text(), reader.metadata


@pytest.mark.parametrize("optional_text", [None, "optional-"])
def test_execute_supports_layered_configuration_and_all_binding_cardinalities(
    discovered_pipeline: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    optional_text: str | None,
) -> None:
    del discovered_pipeline
    setup, setup_reference = create_source(
        tmp_path,
        "setup",
        "setup-",
        capsys,
    )
    required, required_reference = create_source(
        tmp_path,
        "required",
        "required-",
        capsys,
    )
    repeated_values = (("first", "A"), ("second", "B"), ("third", "C"))
    repeated = [
        create_source(tmp_path, name, text, capsys) for name, text in repeated_values
    ]
    expected_references = [setup_reference, required_reference]
    optional_arguments: list[str] = []
    expected_body = "[setup-required-"
    if optional_text is not None:
        optional, optional_reference = create_source(
            tmp_path,
            "optional",
            optional_text,
            capsys,
        )
        optional_arguments = ["--input", f"optional={optional}"]
        expected_references.append(optional_reference)
        expected_body += optional_text
    expected_references.extend(reference for _, reference in repeated)
    expected_body += "ABC]"

    json_configuration = tmp_path / "transform.json"
    yaml_configuration = tmp_path / "transform.yaml"
    json_configuration.write_text(
        json.dumps({"prefix": "[", "suffix": "overridden"}),
        encoding="utf-8",
    )
    yaml_configuration.write_text('suffix: "]"\n', encoding="utf-8")
    transformed = tmp_path / "transformed.provium"
    summary = tmp_path / "summary.provium"
    repeated_arguments = [
        argument for path, _ in repeated for argument in ("--input", f"repeated={path}")
    ]
    arguments = [
        "execute",
        "test.TransformTextV1",
        "--procedure-config",
        str(json_configuration),
        "--procedure-config",
        str(yaml_configuration),
        "--setup-input",
        f"setup={setup}",
        "--input",
        f"required={required}",
        *optional_arguments,
        *repeated_arguments,
        "--output",
        f"transformed={transformed}",
        "--output",
        f"summary={summary}",
    ]

    status, stdout, stderr = invoke(arguments, capsys)

    assert status == 0
    execution_identity = stdout.strip()
    assert str(UUID(execution_identity)) == execution_identity
    assert stderr == ""
    transformed_body, transformed_header = read_output(transformed)
    summary_body, summary_header = read_output(summary)
    assert transformed_body == expected_body
    assert summary_body == (
        f"inputs={len(expected_references)};characters={len(expected_body)}"
    )
    assert transformed_header.lineage == summary_header.lineage
    execution = transformed_header.lineage.executions[execution_identity]
    assert execution.inputs == tuple(expected_references)
    assert set(execution.outputs) == {
        ArtifactReference(
            transformed_header.artifact_identity,
            transformed_header.artifact_identifier,
        ),
        ArtifactReference(
            summary_header.artifact_identity,
            summary_header.artifact_identifier,
        ),
    }
