"""Pipeline actions registered as a native Provium command group."""

from provium.cli.catalog import CommandCatalog
from provium.cli.group import CommandGroup

from .enqueue import EnqueueCommand
from .inspect import InspectCommand
from .list import ListCommand
from .queue import QueueCommand
from .register import RegisterCommand
from .retry import RetryCommand
from .run import RunCommand
from .status import StatusCommand
from .unregister import UnregisterCommand
from .validate import ValidateCommand


class PipelineCommand(CommandGroup):
    name = "pipeline"
    help = "Execute and manage provenance-addressed procedure graphs"
    catalog = CommandCatalog()


for command in (
    ListCommand,
    QueueCommand,
    UnregisterCommand,
    RunCommand,
    RegisterCommand,
    EnqueueCommand,
    StatusCommand,
    ValidateCommand,
    InspectCommand,
    RetryCommand,
):
    PipelineCommand.catalog.register(command)
