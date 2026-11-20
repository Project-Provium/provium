"""Cloud commands participate in the native Provium CLI registry."""

import json
import subprocess
import sys
from importlib.metadata import distribution

import pytest
from provium.cli import CommandGroup, discover_command_catalogs, run

from provium_cloud import cli
from provium_cloud.cli.aws.commands.aws import AWSCommand
from provium_cloud.cli.aws.commands.validate import ValidateCommand


def test_plugin_registration():
    entries = [
        entry
        for entry in distribution("provium-cloud").entry_points
        if entry.group == "provium.cli.command_catalogs"
    ]
    assert len(entries) == 1
    assert entries[0].name == "cloud"
    assert entries[0].load() is cli.plugin
    assert discover_command_catalogs().resolve("cloud") is cli.CloudCommand
    assert issubclass(cli.CloudCommand, CommandGroup)
    assert cli.CloudCommand.catalog.resolve("aws") is AWSCommand
    assert AWSCommand.catalog.resolve("validate") is ValidateCommand
    assert not any(
        entry.group == "console_scripts"
        for entry in distribution("provium-cloud").entry_points
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ["cloud", "--help"],
        ["cloud", "aws", "--help"],
        ["cloud", "aws", "validate", "--help"],
    ],
)
def test_group_and_command_help(arguments, capsys):
    with pytest.raises(SystemExit) as result:
        run(arguments, catalog=cli.catalog)
    assert result.value.code == 0
    assert "provium " + " ".join(arguments[:-1]) in capsys.readouterr().out


@pytest.mark.parametrize(
    "arguments",
    [["cloud"], ["cloud", "aws"], ["cloud", "aws", "validate"], ["cloud", "unknown"]],
)
def test_missing_or_unknown_arguments(arguments):
    with pytest.raises(SystemExit) as result:
        run(arguments, catalog=cli.catalog)
    assert result.value.code == 2


def test_validate_deployment(tmp_path, capsys):
    path = tmp_path / "deployment.yaml"
    path.write_text(
        "provider: aws\nregion: us-east-1\n"
        "artifacts: {backend: custom-artifacts}\n"
        "state: {backend: custom-state}\n",
        encoding="utf-8",
    )
    assert run(["cloud", "aws", "validate", "-d", str(path)]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"valid": True, "provider": "aws"}
    assert not output.err


@pytest.mark.parametrize(
    "content",
    [None, "[broken", "name: forbidden", "provider: aws\nprovider: aws", "1: value"],
)
def test_validation_errors_are_reported_without_tracebacks(tmp_path, capsys, content):
    path = tmp_path / "deployment.yaml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert (
        run(
            ["cloud", "aws", "validate", "--deployment", str(path)], catalog=cli.catalog
        )
        == 2
    )
    output = capsys.readouterr()
    assert not output.out
    assert "validating cloud AWS deployment failed" in output.err
    assert "Traceback" not in output.err


def test_cloud_plugin_import_does_not_load_dispatch_or_aws_sdk():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import provium_cloud.cli, sys; "
            "assert not {'provium_dispatch', 'boto3', 'botocore'} & sys.modules.keys()",
        ],
        check=True,
    )
