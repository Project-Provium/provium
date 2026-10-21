"""Procedure discovery, help, and direct execution command."""

from __future__ import annotations

import argparse
import glob
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from provium import (
    ArtifactReadBinding,
    ArtifactWriteBinding,
    ProcedureDefinition,
    ProcedureExecutor,
    ProcedureIOFieldMetadata,
    discover_procedure_catalogs,
    load_json_configuration,
    load_yaml_configuration,
    session,
)
from provium.files import local_file, normalize_location

from ..command import Command
from ..errors import EXPECTED_CLI_ERRORS, print_cli_error


def _complete_procedure_identifiers(prefix: str, **_: object) -> list[str]:
    try:
        identifiers = discover_procedure_catalogs().definitions
    except Exception:  # noqa: BLE001
        return []
    return sorted(
        identifier for identifier in identifiers if identifier.startswith(prefix)
    )


def _complete_binding(
    prefix: str,
    parsed_args: argparse.Namespace,
    record_name: str,
) -> list[str]:
    identifier = getattr(parsed_args, "identifier", None)
    if not isinstance(identifier, str):
        return []
    try:
        contract = _definition(identifier).resolve_contract()
        record = getattr(contract, record_name)
        fields = (
            dict[str, object]()
            if record is None
            else cast(Mapping[str, object], record.fields)
        )
    except Exception:  # noqa: BLE001
        return []
    field_name, separator, path_prefix = prefix.partition("=")
    if not separator:
        return [f"{name}=" for name in fields if name.startswith(field_name)]
    if field_name not in fields:
        return []
    completions: list[str] = []
    for value in sorted(glob.glob(f"{glob.escape(path_prefix)}*")):
        path = Path(value)
        suffix = "/" if path.is_dir() else ""
        completions.append(f"{field_name}={value}{suffix}")
    return completions


def _complete_setup_bindings(
    prefix: str,
    *,
    parsed_args: argparse.Namespace,
    **_: object,
) -> list[str]:
    return _complete_binding(prefix, parsed_args, "SetupInputs")


def _complete_input_bindings(
    prefix: str,
    *,
    parsed_args: argparse.Namespace,
    **_: object,
) -> list[str]:
    return _complete_binding(prefix, parsed_args, "Inputs")


def _complete_output_bindings(
    prefix: str,
    *,
    parsed_args: argparse.Namespace,
    **_: object,
) -> list[str]:
    return _complete_binding(prefix, parsed_args, "Outputs")


def _definition(identifier: str) -> ProcedureDefinition[Any]:
    try:
        return discover_procedure_catalogs().resolve(identifier)
    except KeyError:
        raise ValueError(f"unknown procedure: {identifier}") from None


class ExecuteCommand(Command):
    """Execute one discovered procedure directly."""

    name = "execute"
    help = "Execute a procedure"
    add_help = False

    def configure(self, parser: argparse.ArgumentParser) -> None:
        self._parser = parser
        identifier = parser.add_argument("identifier", nargs="?")
        identifier.completer = _complete_procedure_identifiers  # type: ignore[attr-defined]
        parser.add_argument(
            "-h",
            "--help",
            action="store_true",
            help="Show command or procedure help",
        )
        parser.add_argument(
            "-l",
            "--list",
            action="store_true",
            help="List available procedures",
        )
        parser.add_argument("--procedure-config", action="append", default=[])
        parser.add_argument("--scratch-directory")
        setup_input = parser.add_argument("--setup-input", action="append", default=[])
        setup_input.completer = _complete_setup_bindings  # type: ignore[attr-defined]
        process_input = parser.add_argument("--input", action="append", default=[])
        process_input.completer = _complete_input_bindings  # type: ignore[attr-defined]
        output_binding = parser.add_argument("--output", action="append", default=[])
        output_binding.completer = _complete_output_bindings  # type: ignore[attr-defined]

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            if arguments.list:
                if arguments.identifier is not None:
                    self._parser.error("identifier cannot be used with --list")
                return self._list()
            if arguments.identifier is None:
                if arguments.help:
                    self._parser.print_help()
                    return 0
                self._parser.error("the following arguments are required: identifier")
            if arguments.help:
                return self._show_help(arguments.identifier)
            with session(scratch_directory=arguments.scratch_directory):
                definition = _definition(arguments.identifier)
                contract = definition.resolve_contract()
                layers = tuple(
                    self._load_configuration(path)
                    for path in arguments.procedure_config
                )
                setup_inputs = self._read_bindings(
                    contract.SetupInputs.fields if contract.SetupInputs else {},
                    arguments.setup_input,
                )
                inputs = self._read_bindings(
                    contract.Inputs.fields if contract.Inputs else {},
                    arguments.input,
                )
                outputs = self._write_bindings(
                    contract.Outputs.fields if contract.Outputs else {},
                    arguments.output,
                )
                result = ProcedureExecutor().execute(
                    definition,
                    configuration_layers=layers,
                    setup_inputs=setup_inputs,
                    inputs=inputs,
                    outputs=outputs,
                )
        except EXPECTED_CLI_ERRORS as error:
            identifier = getattr(arguments, "identifier", None)
            context = (
                "executing procedure"
                if identifier is None
                else f"executing procedure {identifier!r}"
            )
            return print_cli_error(context, error)
        print(result.identity)
        return 0

    @staticmethod
    def _list() -> int:
        definitions = discover_procedure_catalogs().definitions
        for identifier in sorted(definitions):
            definition = definitions[identifier]
            print(f"{identifier}\t{definition.label}")
        return 0

    @staticmethod
    def _show_help(identifier: str) -> int:
        definition = _definition(identifier)
        contract = definition.resolve_contract()
        print(f"{definition.label} ({definition.identifier})")
        if definition.description is not None:
            print(definition.description)
        print("\nInvocation:")
        print(definition.invocation_synopsis)
        ExecuteCommand._print_fields(
            "Setup inputs", contract.metadata.setup_inputs, "Accepts"
        )
        ExecuteCommand._print_fields("Inputs", contract.metadata.inputs, "Accepts")
        ExecuteCommand._print_fields("Outputs", contract.metadata.outputs, "Produces")
        schema = contract.metadata.configuration_schema
        if schema is not None:
            print("\nConfiguration:")
            print(json.dumps(schema, indent=2, sort_keys=True))
        return 0

    @staticmethod
    def _print_fields(
        heading: str,
        fields: tuple[ProcedureIOFieldMetadata, ...],
        quantity_verb: str,
    ) -> None:
        if not fields:
            return
        print(f"\n{heading}:")
        quantity_label = f"{quantity_verb}:"
        for field in fields:
            quantity = ExecuteCommand._format_cardinality(
                field.minimum,
                field.maximum,
            )
            print(f"  {field.name}")
            print(f"    Artifact: {field.artifact_identifier}")
            print(f"    {quantity_label:<9} {quantity}")

    @staticmethod
    def _format_cardinality(minimum: int, maximum: int | None) -> str:
        if maximum == minimum:
            return f"exactly {minimum}"
        if maximum is None:
            return "any number" if minimum == 0 else f"{minimum} or more"
        if minimum == 0 and maximum == 1:
            return "0 or 1"
        return f"{minimum} to {maximum}"

    @staticmethod
    def _load_configuration(path_value: str) -> Mapping[str, object]:
        with local_file(path_value) as path:
            if path.suffix.casefold() == ".json":
                return load_json_configuration(path)
            if path.suffix.casefold() in {".yaml", ".yml"}:
                return load_yaml_configuration(path)
        raise ValueError(f"unsupported configuration file type: {path_value}")

    @staticmethod
    def _assignments(values: Sequence[str]) -> dict[str, list[Path | str]]:
        assignments: dict[str, list[Path | str]] = {}
        for value in values:
            name, separator, path = value.partition("=")
            if not separator or not name or not path:
                raise ValueError(f"binding must use FIELD=PATH syntax: {value}")
            assignments.setdefault(name, []).append(normalize_location(path))
        return assignments

    @classmethod
    def _read_bindings(
        cls,
        fields: Mapping[str, Any],
        values: Sequence[str],
    ) -> dict[str, ArtifactReadBinding[Any] | list[ArtifactReadBinding[Any]]]:
        result: dict[
            str, ArtifactReadBinding[Any] | list[ArtifactReadBinding[Any]]
        ] = {}
        for name, paths in cls._assignments(values).items():
            field = cls._field(fields, name)
            artifact = field.artifact.resolve()
            bindings = [artifact.bind_read(path) for path in paths]
            result[name] = bindings if field.repeated else cls._single(name, bindings)
        return result

    @classmethod
    def _write_bindings(
        cls,
        fields: Mapping[str, Any],
        values: Sequence[str],
    ) -> dict[str, ArtifactWriteBinding[Any]]:
        result: dict[str, ArtifactWriteBinding[Any]] = {}
        for name, paths in cls._assignments(values).items():
            field = cls._field(fields, name)
            artifact = field.artifact.resolve()
            result[name] = artifact.bind_write(cls._single(name, paths))
        return result

    @staticmethod
    def _field(fields: Mapping[str, Any], name: str) -> Any:
        try:
            return fields[name]
        except KeyError:
            raise ValueError(f"unknown binding field: {name}") from None

    @staticmethod
    def _single[T](name: str, values: Sequence[T]) -> T:
        if len(values) != 1:
            raise ValueError(f"binding field {name} may be supplied only once")
        return values[0]


__all__ = ["ExecuteCommand"]
