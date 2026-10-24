"""Durable input management commands."""

from provium.cli.catalog import CommandCatalog
from provium.cli.group import CommandGroup

from .enqueue import EnqueueCommand
from .input_set import InputSetCommand
from .inspect import InspectCommand
from .list import ListCommand
from .lookup import LookupCommand
from .queue import QueueCommand
from .register import RegisterCommand
from .retry import RetryCommand
from .run import RunCommand
from .status import StatusCommand
from .unregister import UnregisterCommand
from .validate import ValidateCommand


class DispatchCommand(CommandGroup):
    name = "dispatch"
    help = "Manage persistent inputs for procedure pipelines"
    catalog = CommandCatalog()


for command in (
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
