"""Tests for the built-in execute command."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from provium import (
    Artifact,
    ArtifactDefinition,
    ArtifactReader,
    ArtifactWriter,
    Procedure,
    ProcedureCatalog,
    ProcedureConfig,
    ProcedureContract,
    ProcedureDefinition,
    ProcedureExecutionResult,
    ProcedureInputs,
    ProcedureOutputs,
    ProcedureProcessContext,
    input,
    optional_input,
    output,
    repeated_input,
)
from provium.cli import run
from provium.cli.commands.execute import (
    ExecuteCommand,
    _complete_input_bindings,
    _complete_output_bindings,
    _complete_procedure_identifiers,
    _complete_setup_bindings,
)


class Reader(ArtifactReader):
    pass


class Writer(ArtifactWriter):
    pass


ARTIFACT = ArtifactDefinition(
    "example.DataV1", f"{__name__}:DataArtifact", "Example data"
)


class DataArtifact(Artifact[Reader, Writer]):
    definition = ARTIFACT
    reader = Reader
    writer = Writer


class Config(ProcedureConfig):
    value: int = 1


class Contract(ProcedureContract[Config]):
    configuration = Config

    class SetupInputs(ProcedureInputs):
        model = input(ARTIFACT)

    class Inputs(ProcedureInputs):
        source = input(ARTIFACT)
        previous = optional_input(ARTIFACT)
        extras = repeated_input(ARTIFACT, minimum=1)

    class Outputs(ProcedureOutputs):
        result = output(ARTIFACT)


class EmptyContract(ProcedureContract[None]):
    configuration = None


DEFINITION = ProcedureDefinition(
    "example.ProcessV1",
    f"{__name__}:Implementation",
    "Process data",
    "Process example data.",
    Contract,
)


class Implementation(
    Procedure[Config, Contract.SetupInputs, Contract.Inputs, Contract.Outputs]
):
    definition = DEFINITION

    def process(
        self,
        context: ProcedureProcessContext,
        configuration: Config,
        inputs: Contract.Inputs,
        outputs: Contract.Outputs,
    ) -> None:
        pass


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> ProcedureCatalog:
    result = ProcedureCatalog()
    result.register(DEFINITION)
    monkeypatch.setattr(
        "provium.cli.commands.execute.discover_procedure_catalogs",
        lambda: result,
    )
    return result


def test_execute_list_is_sorted_and_does_not_resolve(
    catalog: ProcedureCatalog,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        ProcedureDefinition,
        "resolve",
        lambda self: pytest.fail("quick listing must not resolve"),
    )

    assert run(["execute", "-l"]) == 0
    assert capsys.readouterr().out == "example.ProcessV1\tProcess data\n"


def test_execute_help_renders_lazy_contract_metadata(
    catalog: ProcedureCatalog,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        ProcedureDefinition,
        "resolve",
        lambda self: pytest.fail("quick show must not resolve"),
    )

    assert run(["execute", DEFINITION.identifier, "--help"]) == 0
    output_text = capsys.readouterr().out
    assert "Process data" in output_text
    assert "Process example data." in output_text
    assert DEFINITION.invocation_synopsis in output_text
    assert "example.DataV1" in output_text
    assert '"value"' in output_text


def test_execute_help_formats_input_and_output_fields_vertically(
    catalog: ProcedureCatalog,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del catalog

    assert run(["execute", DEFINITION.identifier, "--help"]) == 0

    output_text = capsys.readouterr().out
    assert (
        "Inputs:\n  source\n    Artifact: example.DataV1\n    Accepts:  exactly 1\n"
    ) in output_text
    assert (
        "Outputs:\n  result\n    Artifact: example.DataV1\n    Produces: exactly 1\n"
    ) in output_text


@pytest.mark.parametrize(
    ("minimum", "maximum", "display"),
    [
        (1, 1, "exactly 1"),
        (0, 1, "0 or 1"),
        (1, None, "1 or more"),
        (0, None, "any number"),
        (2, 5, "2 to 5"),
        (3, 3, "exactly 3"),
    ],
)
def test_procedure_help_formats_io_cardinality(
    minimum: int,
    maximum: int | None,
    display: str,
) -> None:
    assert ExecuteCommand._format_cardinality(minimum, maximum) == display


def test_execute_short_help_flag_renders_procedure_help(
    catalog: ProcedureCatalog,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run(["execute", DEFINITION.identifier, "-h"]) == 0
    assert DEFINITION.invocation_synopsis in capsys.readouterr().out


def test_execute_help_without_identifier_renders_command_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run(["execute", "--help"]) == 0
    assert "-l, --list" in capsys.readouterr().out


def test_execute_list_rejects_an_identifier(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as caught:
        run(["execute", DEFINITION.identifier, "-l"])
    assert caught.value.code == 2
    assert "cannot be used with --list" in capsys.readouterr().err


def test_unknown_procedure_reports_a_cli_error(
    catalog: ProcedureCatalog,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run(["execute", "missing", "--help"]) == 2
    assert "unknown procedure: missing" in capsys.readouterr().err


def test_procedure_contract_attribute_failure_is_reported_as_a_cli_error(
    catalog: ProcedureCatalog,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del catalog

    def fail(self: ProcedureDefinition[Any]) -> type[Any]:
        del self
        raise AttributeError("module has no attribute 'MissingContract'")

    monkeypatch.setattr(ProcedureDefinition, "resolve_contract", fail)

    assert run(["execute", DEFINITION.identifier, "--help"]) == 2
    diagnostic = capsys.readouterr().err
    assert f"executing procedure '{DEFINITION.identifier}' failed" in diagnostic
    assert "AttributeError" in diagnostic
    assert "MissingContract" in diagnostic


def test_help_handles_empty_contract_without_description(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    definition = ProcedureDefinition(
        "example.EmptyV1",
        f"{__name__}:Implementation",
        "Empty",
        None,
        EmptyContract,
    )
    discovered = ProcedureCatalog()
    discovered.register(definition)
    monkeypatch.setattr(
        "provium.cli.commands.execute.discover_procedure_catalogs",
        lambda: discovered,
    )

    assert run(["execute", definition.identifier, "--help"]) == 0
    assert "Configuration:" not in capsys.readouterr().out


def test_execute_builds_layered_typed_bindings(
    catalog: ProcedureCatalog,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configuration = tmp_path / "config.json"
    configuration.write_text(json.dumps({"value": 4}))
    received: dict[str, Any] = {}

    def execute(self: object, definition: object, **arguments: object) -> object:
        received.update(arguments)
        return ProcedureExecutionResult("execution", None, ())

    monkeypatch.setattr(
        "provium.cli.commands.execute.ProcedureExecutor.execute", execute
    )

    assert (
        run(
            [
                "execute",
                DEFINITION.identifier,
                "--procedure-config",
                str(configuration),
                "--setup-input",
                "model=model.pa",
                "--input",
                "source=source.pa",
                "--input",
                "extras=first.pa",
                "--input",
                "extras=second.pa",
                "--output",
                "result=result.pa",
            ]
        )
        == 0
    )
    assert received["configuration_layers"] == ({"value": 4},)
    assert received["setup_inputs"]["model"].path == Path("model.pa")
    assert received["inputs"]["source"].path == Path("source.pa")
    assert [binding.path for binding in received["inputs"]["extras"]] == [
        Path("first.pa"),
        Path("second.pa"),
    ]
    assert received["outputs"]["result"].path == Path("result.pa")
    assert capsys.readouterr().out == "execution\n"


def test_execute_reports_invalid_binding_syntax(
    catalog: ProcedureCatalog,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run(["execute", DEFINITION.identifier, "--input", "invalid"]) == 2
    assert "FIELD=PATH" in capsys.readouterr().err


def test_execute_reports_runtime_failures_as_cli_errors(
    catalog: ProcedureCatalog,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("processing failed")

    monkeypatch.setattr("provium.cli.commands.execute.ProcedureExecutor.execute", fail)

    assert run(["execute", DEFINITION.identifier]) == 2
    diagnostic = capsys.readouterr().err
    assert f"executing procedure '{DEFINITION.identifier}' failed" in diagnostic
    assert "RuntimeError" in diagnostic
    assert "processing failed" in diagnostic


def test_configuration_loader_supports_yaml_and_rejects_unknown_suffix(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("value: 3\n")

    assert ExecuteCommand._load_configuration(str(path)) == {"value": 3}
    with pytest.raises(ValueError, match="unsupported configuration"):
        ExecuteCommand._load_configuration(str(tmp_path / "config.txt"))


def test_binding_helpers_reject_unknown_and_duplicate_fields() -> None:
    with pytest.raises(ValueError, match="unknown binding field"):
        ExecuteCommand._read_bindings(Contract.Inputs.fields, ["missing=value.pa"])
    with pytest.raises(ValueError, match="may be supplied only once"):
        ExecuteCommand._read_bindings(
            Contract.Inputs.fields,
            ["source=first.pa", "source=second.pa"],
        )


def test_execute_requires_a_procedure_or_list_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as caught:
        run(["execute"])
    assert caught.value.code == 2
    assert "identifier" in capsys.readouterr().err


def test_completion_suggests_discovered_procedure_identifiers(
    catalog: ProcedureCatalog,
) -> None:
    del catalog

    assert _complete_procedure_identifiers("example.P") == ["example.ProcessV1"]
    assert _complete_procedure_identifiers("missing") == []


def test_completion_suggests_contract_binding_fields(
    catalog: ProcedureCatalog,
) -> None:
    del catalog
    arguments = Namespace(identifier=DEFINITION.identifier)

    assert _complete_setup_bindings("m", parsed_args=arguments) == ["model="]
    assert _complete_input_bindings("", parsed_args=arguments) == [
        "source=",
        "previous=",
        "extras=",
    ]
    assert _complete_output_bindings("r", parsed_args=arguments) == ["result="]


def test_binding_completion_continues_with_filesystem_paths(
    catalog: ProcedureCatalog,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    del catalog
    monkeypatch.chdir(tmp_path)
    (tmp_path / "source-one.pa").touch()
    (tmp_path / "source-two.pa").touch()
    (tmp_path / "source-directory").mkdir()
    (tmp_path / "unrelated.pa").touch()
    arguments = Namespace(identifier=DEFINITION.identifier)

    assert _complete_input_bindings("source=source-", parsed_args=arguments) == [
        "source=source-directory/",
        "source=source-one.pa",
        "source=source-two.pa",
    ]


def test_binding_completion_is_empty_without_a_known_procedure(
    catalog: ProcedureCatalog,
) -> None:
    del catalog

    assert _complete_input_bindings("", parsed_args=Namespace(identifier=None)) == []
    assert (
        _complete_input_bindings("", parsed_args=Namespace(identifier="missing")) == []
    )
    assert (
        _complete_input_bindings(
            "missing=value",
            parsed_args=Namespace(identifier=DEFINITION.identifier),
        )
        == []
    )


def test_completion_ignores_discovery_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> None:
        raise RuntimeError("discovery failed")

    monkeypatch.setattr(
        "provium.cli.commands.execute.discover_procedure_catalogs", fail
    )

    assert _complete_procedure_identifiers("") == []
