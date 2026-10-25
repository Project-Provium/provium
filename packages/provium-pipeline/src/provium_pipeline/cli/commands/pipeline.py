"""Pipeline actions registered as a native Provium command group."""

from provium.cli.catalog import CommandCatalog
from provium.cli.group import CommandGroup

from provium_pipeline.cli.commands.cache import CacheCommand
from provium_pipeline.cli.commands.inspect import InspectCommand
from provium_pipeline.cli.commands.lookup import LookupCommand
from provium_pipeline.cli.commands.run import RunCommand
from provium_pipeline.cli.commands.status import StatusCommand
from provium_pipeline.cli.commands.validate import ValidateCommand


class PipelineCommand(CommandGroup):
    name = "pipeline"
    help = "Execute and manage provenance-addressed procedure graphs"
    catalog = CommandCatalog()


for command in (
    CacheCommand,
    LookupCommand,
    RunCommand,
    StatusCommand,
    ValidateCommand,
    InspectCommand,
):
    PipelineCommand.catalog.register(command)
