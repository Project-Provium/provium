"""Consistent JSON results and error reporting for AWS actions."""

import argparse
import json
from abc import abstractmethod
from typing import Any, ClassVar

from provium.cli.command import Command
from provium.cli.errors import print_cli_error

from provium_cloud.cli.aws.arguments import json_result
from provium_cloud.state.models import Run


class AWSAction(Command):
    fail_on_run_error: ClassVar[bool] = False

    def execute(self, arguments: argparse.Namespace) -> int:
        try:
            result = self.action(arguments)
            if result is not None:
                print(json.dumps(json_result(result)))
            return int(
                self.fail_on_run_error
                and isinstance(result, Run)
                and result.status != "succeeded"
            )
        except Exception as error:
            return print_cli_error(f"cloud AWS {self.name}", error)

    @abstractmethod
    def action(self, arguments: argparse.Namespace) -> Any: ...
