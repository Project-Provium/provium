"""Durable input management commands."""

from provium.cli.catalog import CommandCatalog
from provium.cli.group import CommandGroup

from .input_set import InputSetCommand
from .lookup import LookupCommand


class DispatchCommand(CommandGroup):
    name = "dispatch"
    help = "Manage persistent inputs for procedure pipelines"
    catalog = CommandCatalog()


for command in (InputSetCommand, LookupCommand):
    DispatchCommand.catalog.register(command)
