"""Durable input management commands."""

from provium.cli.catalog import CommandCatalog
from provium.cli.group import CommandGroup

from provium_dispatch.cli.commands.cache import CacheCommand
from provium_dispatch.cli.commands.enqueue import EnqueueCommand
from provium_dispatch.cli.commands.input_set import InputSetCommand
from provium_dispatch.cli.commands.inspect import InspectCommand
from provium_dispatch.cli.commands.list import ListCommand
from provium_dispatch.cli.commands.lookup import LookupCommand
from provium_dispatch.cli.commands.queue import QueueCommand
from provium_dispatch.cli.commands.register import RegisterCommand
from provium_dispatch.cli.commands.retry import RetryCommand
from provium_dispatch.cli.commands.run import RunCommand
from provium_dispatch.cli.commands.status import StatusCommand
from provium_dispatch.cli.commands.unregister import UnregisterCommand
from provium_dispatch.cli.commands.validate import ValidateCommand


class DispatchCommand(CommandGroup):
    name = "dispatch"
    help = "Manage persistent inputs for procedure pipelines"
    catalog = CommandCatalog()


for command in (
    CacheCommand,
    RunCommand,
    StatusCommand,
    InspectCommand,
    ValidateCommand,
    InputSetCommand,
    LookupCommand,
    RegisterCommand,
    UnregisterCommand,
    EnqueueCommand,
    RetryCommand,
    ListCommand,
    QueueCommand,
):
    DispatchCommand.catalog.register(command)
