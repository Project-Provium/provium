"""Commands provided by the Provium CLI distribution."""

from provium.cli.catalog import CommandCatalog
from provium.cli.commands.artifact import ArtifactCommand
from provium.cli.commands.execute import ExecuteCommand
from provium.cli.commands.graph import GraphCommand

catalog = CommandCatalog()
catalog.register(ExecuteCommand)
catalog.register(ArtifactCommand)
catalog.register(GraphCommand)

__all__ = ["catalog"]
