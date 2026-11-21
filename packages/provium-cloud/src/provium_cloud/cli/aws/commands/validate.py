"""Validate an AWS deployment configuration without contacting its backends."""

import argparse
import json

from provium.cli.command import Command
from provium.cli.errors import EXPECTED_CLI_ERRORS, print_cli_error


class ValidateCommand(Command):
    name = "validate"
    help = "Validate deployment configuration locally, without connecting to AWS"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--deployment", "-d", required=True, help="Deployment YAML file"
        )

    def execute(self, arguments: argparse.Namespace) -> int:
        import yaml

        from provium_cloud.deployment import load_deployment

        try:
            deployment = load_deployment(arguments.deployment)
        except (*EXPECTED_CLI_ERRORS, yaml.YAMLError) as error:
            return print_cli_error("validating cloud AWS deployment", error)
        print(json.dumps({"valid": True, "provider": deployment.provider}))
        return 0
