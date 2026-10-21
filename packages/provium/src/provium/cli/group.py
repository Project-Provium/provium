"""Command catalogs nested under commands, at any depth."""

from __future__ import annotations

import argparse
from typing import ClassVar

from .catalog import CommandCatalog
from .command import Command

COMMAND_DESTINATION = "_provium_command"


def configure_catalog(
    parser: argparse.ArgumentParser,
    catalog: CommandCatalog,
    *,
    destination: str = argparse.SUPPRESS,
) -> None:
    subparsers = parser.add_subparsers(dest=destination, required=True)
    for command_type in catalog.commands.values():
        command = command_type()
        child = subparsers.add_parser(
            command.name, help=command.help, add_help=command.add_help
        )
        from .configuration import configure_configuration

        configure_configuration(child)
        command.configure(child)
        child.set_defaults(**{COMMAND_DESTINATION: command})


class CommandGroup(Command):
    """A command whose catalog contains leaf commands or further groups."""

    catalog: ClassVar[CommandCatalog]

    def configure(self, parser: argparse.ArgumentParser) -> None:
        configure_catalog(parser, self.catalog)

    def execute(self, arguments: argparse.Namespace) -> int:
        command: Command = getattr(arguments, COMMAND_DESTINATION)
        return command.execute(arguments)


__all__ = ["CommandGroup"]
