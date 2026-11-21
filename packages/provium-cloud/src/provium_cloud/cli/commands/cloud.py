"""Cloud provider commands registered under provium cloud."""

from provium.cli.catalog import CommandCatalog
from provium.cli.group import CommandGroup

from provium_cloud.cli.aws import AWSCommand


class CloudCommand(CommandGroup):
    name = "cloud"
    help = "Configure cloud execution of procedures and pipelines"
    catalog = CommandCatalog()


CloudCommand.catalog.register(AWSCommand)
