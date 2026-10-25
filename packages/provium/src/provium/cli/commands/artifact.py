"""Artifact import and export commands."""

from __future__ import annotations

import argparse
from typing import Any

from provium import (
    ImperativeProcedure,
    discover_artifact_catalogs,
    read_artifact_header,
    session,
)
from provium.cli.command import Command
from provium.cli.errors import EXPECTED_CLI_ERRORS, print_cli_error
from provium.files import local_file, normalize_location, writable_file

_LOAD_PROCEDURE_IDENTIFIER = "provium.builtin.LoadArtifactV1"
_LOAD_PROCEDURE_CONTRACT_DIGEST = (
    "17acdbf8b06ab63f7fce758374716414892a81d79fb313f8323013029ff4af24"
)


def _complete_artifact_identifiers(prefix: str, **_: object) -> list[str]:
    try:
        identifiers = discover_artifact_catalogs().definitions
    except Exception:  # noqa: BLE001
        return []
    return sorted(
        identifier for identifier in identifiers if identifier.startswith(prefix)
    )


def _artifact(identifier: str) -> type[Any]:
    try:
        definition = discover_artifact_catalogs().resolve(identifier)
    except KeyError:
        raise ValueError(f"unknown artifact: {identifier}") from None
    return definition.resolve()


class ArtifactCommand(Command):
    """Provide operations for working with artifacts."""

    name = "artifact"
    help = "Manage artifacts"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        actions = parser.add_subparsers(dest="artifact_action", required=True)
        for action in ("dump", "load"):
            action_parser = actions.add_parser(
                action, help=f"{action.title()} an artifact"
            )
            if action == "load":
                identifier = action_parser.add_argument("identifier")
                identifier.completer = _complete_artifact_identifiers  # type: ignore[attr-defined]
            action_parser.add_argument("source", type=normalize_location)
            action_parser.add_argument("destination", type=normalize_location)
            action_parser.add_argument("--scratch-directory")
            action_parser.set_defaults(artifact_handler=getattr(self, f"_{action}"))

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            with session(scratch_directory=arguments.scratch_directory):
                arguments.artifact_handler(arguments)
        except EXPECTED_CLI_ERRORS as error:
            action = arguments.artifact_action
            identifier = getattr(arguments, "identifier", None)
            if action == "dump":
                context = f"dumping artifact from {str(arguments.source)!r}"
            else:
                context = f"loading artifact {identifier!r}"
            return print_cli_error(context, error)
        return 0

    @staticmethod
    def _dump(arguments: argparse.Namespace) -> None:
        identifier = read_artifact_header(arguments.source).artifact_identifier
        artifact = _artifact(identifier)
        handler = artifact.dump
        if handler is None:
            raise ValueError(f"artifact {identifier} does not define a dump handler")
        with session():
            with artifact.bind_read(arguments.source).open() as reader:
                with writable_file(arguments.destination) as destination:
                    handler(reader, destination)

    @staticmethod
    def _load(arguments: argparse.Namespace) -> None:
        artifact = _artifact(arguments.identifier)
        handler = artifact.load
        if handler is None:
            raise ValueError(
                f"artifact {arguments.identifier} does not define a load handler"
            )
        binding = artifact.bind_write(arguments.destination)
        procedure = ImperativeProcedure(
            _LOAD_PROCEDURE_IDENTIFIER,
            _LOAD_PROCEDURE_CONTRACT_DIGEST,
        )
        with procedure.execute(outputs={"artifact": binding}):
            with binding.open() as writer:
                with local_file(arguments.source) as source:
                    handler(writer, source)


__all__ = ["ArtifactCommand"]
