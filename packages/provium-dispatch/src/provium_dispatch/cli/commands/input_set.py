"""Manage names for input sets independently of pipeline execution."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from provium.cli.catalog import CommandCatalog
from provium.cli.command import Command
from provium.cli.group import CommandGroup

from provium_dispatch.cli.arguments import (
    apply_configuration,
    assignments,
    configure_storage,
)

if TYPE_CHECKING:
    from provium_pipeline.store import ArtifactStore

    from provium_dispatch.input_sets import InputSetIndex


def open_index(arguments: argparse.Namespace, store: ArtifactStore) -> InputSetIndex:
    if arguments.database:
        from provium_dispatch.persistence.factory import open_backend

        return open_backend(arguments.database)
    from provium_dispatch.input_sets import LocalInputSetIndex

    return LocalInputSetIndex(store.root)


class InputSetAction(Command):
    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_storage(parser)
        if self.name in ("register", "resolve"):
            parser.add_argument("name")
        elif self.name == "unregister":
            parser.add_argument("name", nargs="?")
        if self.name != "resolve":
            parser.add_argument(
                "--input", action="append", default=[], metavar="NAME=VALUE"
            )
            parser.add_argument(
                "--input-type",
                action="append",
                default=[],
                metavar="NAME=CODEC",
                help="Declare a value codec; undeclared inputs are artifact files",
            )

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            from provium_pipeline.input_sets import input_set_hash, inspect_inputs
            from provium_pipeline.store import ArtifactStore

            apply_configuration(arguments)
            if self.name == "unregister" and bool(arguments.name) == bool(
                arguments.input
            ):
                raise ValueError("select a name or --input bindings")  # noqa: TRY301
            with ArtifactStore(
                arguments.store, scratch=arguments.scratch_directory
            ) as store:
                declarations = {
                    name: {"value_type": codec}
                    for name, codec in assignments(
                        getattr(arguments, "input_type", [])
                    ).items()
                }
                bindings = assignments(getattr(arguments, "input", []))
                if declarations.keys() - bindings.keys():
                    raise ValueError("input-type names must have an input binding")  # noqa: TRY301
                inputs = (
                    inspect_inputs(store, bindings, declarations)
                    if self.name in ("register", "find")
                    or (self.name == "unregister" and arguments.input)
                    else None
                )
                index = open_index(arguments, store)
                if self.name == "register":
                    assert inputs is not None
                    result = index.register_input_set(arguments.name, inputs)
                elif self.name == "resolve":
                    result = index.resolve_input_set(name=arguments.name)
                elif self.name == "find":
                    assert inputs is not None
                    result = index.find_input_sets(input_set_hash(inputs))
                else:
                    result = index.unregister_input_sets(
                        name=arguments.name,
                        key=input_set_hash(inputs) if inputs is not None else None,
                    )
            if arguments.json:
                print(json.dumps(result))
            elif isinstance(result, list):
                print("\n".join(result) if result else "No matching input-set names.")
            else:
                print(f"Input set: {result['hash']}")
                for name, item in result["inputs"].items():
                    value = (
                        item["value_input"]
                        if "value_input" in item
                        else item["location"]
                    )
                    print(f"  {name}: {value}")
        except Exception as error:
            print(f"provium dispatch input-set: {error}", file=sys.stderr)
            return 2
        return 0


class RegisterInputSetCommand(InputSetAction):
    name = "register"
    help = "Name an input set using named artifact files"


class ResolveInputSetCommand(InputSetAction):
    name = "resolve"
    help = "Resolve a name to its input-set hash and recorded files"


class FindInputSetCommand(InputSetAction):
    name = "find"
    help = "Find names matching the fingerprints of input files"


class UnregisterInputSetCommand(InputSetAction):
    name = "unregister"
    help = "Remove a name, or every name matching input files; preserve artifacts"


class InputSetCommand(CommandGroup):
    name = "input-set"
    help = "Register, resolve, find, and unregister named input sets"
    catalog = CommandCatalog()


for command in (
    RegisterInputSetCommand,
    ResolveInputSetCommand,
    FindInputSetCommand,
    UnregisterInputSetCommand,
):
    InputSetCommand.catalog.register(command)
