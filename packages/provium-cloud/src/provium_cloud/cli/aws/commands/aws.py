"""AWS actions registered as native Provium commands."""

from provium.cli.catalog import CommandCatalog
from provium.cli.group import CommandGroup

from provium_cloud.cli.aws.commands.validate import ValidateCommand


class AWSCommand(CommandGroup):
    name = "aws"
    help = "Configure execution on AWS Batch"
    catalog = CommandCatalog()


AWSCommand.catalog.register(ValidateCommand)
