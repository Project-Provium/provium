"""Pipeline actions registered as a native Provium command group."""

from provium.cli.catalog import CommandCatalog
from provium.cli.group import CommandGroup

from .inspect import InspectCommand
from .lookup import LookupCommand
from .run import RunCommand
from .status import StatusCommand
from .validate import ValidateCommand


class PipelineCommand(CommandGroup):
    name = "pipeline"
    help = "Execute and manage provenance-addressed procedure graphs"
    catalog = CommandCatalog()


for command in (
    LookupCommand,
    RunCommand,
    StatusCommand,
    ValidateCommand,
    InspectCommand,
):
    PipelineCommand.catalog.register(command)
