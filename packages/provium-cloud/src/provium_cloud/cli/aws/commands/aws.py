"""AWS actions registered as native Provium commands."""

from provium.cli.catalog import CommandCatalog
from provium.cli.group import CommandGroup

from provium_cloud.cli.aws.commands.artifacts import FetchCommand, LookupCommand
from provium_cloud.cli.aws.commands.execution import (
    ControllerCommand,
    LogsCommand,
    WorkerCommand,
)
from provium_cloud.cli.aws.commands.invocations import EnqueueCommand, PlanCommand
from provium_cloud.cli.aws.commands.runs import (
    CancelCommand,
    InspectCommand,
    ListCommand,
    RetryCommand,
    StatusCommand,
    WaitCommand,
)
from provium_cloud.cli.aws.commands.validate import ValidateCommand


class AWSCommand(CommandGroup):
    name = "aws"
    help = "Schedule and manage execution on AWS Batch"
    catalog = CommandCatalog()


AWSCommand.catalog.register(ValidateCommand)

for command in (
    PlanCommand,
    EnqueueCommand,
    ListCommand,
    StatusCommand,
    InspectCommand,
    CancelCommand,
    RetryCommand,
    WaitCommand,
    LookupCommand,
    FetchCommand,
    LogsCommand,
    ControllerCommand,
    WorkerCommand,
):
    AWSCommand.catalog.register(command)
