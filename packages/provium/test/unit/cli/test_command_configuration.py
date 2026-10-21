"""Global command configuration overrides."""

import argparse
from pathlib import Path

import pytest

from provium.cli.application import create_parser, run
from provium.cli.catalog import CommandCatalog
from provium.cli.command import Command
from provium.cli.group import CommandGroup
from provium.configuration import load_configuration


class SettingsCommand(Command):
    name = "settings"
    help = "Read settings"
    result: dict[str, object] = {}

    def configure(self, parser: argparse.ArgumentParser) -> None:
        pass

    def execute(self, arguments: argparse.Namespace) -> int:
        type(self).result = self.configuration(arguments)
        return 0


def catalog() -> CommandCatalog:
    leaves = CommandCatalog()
    leaves.register(SettingsCommand)

    class Group(CommandGroup):
        name = "group"
        help = "Group"
        catalog = leaves

    commands = CommandCatalog()
    commands.register(Group)
    return commands


def test_overrides_across_command_levels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PROVIUM_CONFIG__PIPELINE__ABC", "environment")
    assert (
        run(
            [
                "--config",
                "pipeline.abc=root",
                "group",
                "--config",
                "pipeline.abc=group",
                "settings",
                "--config",
                "pipeline.abc=xyz",
                "--config",
                "pipeline.workers=3",
            ],
            catalog=catalog(),
        )
        == 0
    )
    assert SettingsCommand.result == {"pipeline": {"abc": "xyz", "workers": 3}}
    assert run(["group", "settings"], catalog=catalog()) == 0
    assert SettingsCommand.result == {"pipeline": {"abc": "environment"}}


@pytest.mark.parametrize("value", ["missing", "=value", "a..b=x", "a.=x", "a b=x"])
def test_invalid_override_rejected_before_execution(value: str) -> None:
    with pytest.raises(SystemExit) as error:
        run(["group", "settings", "--config", value], catalog=catalog())
    assert error.value.code == 2


def test_help_does_not_load_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / ".provium"
    directory.mkdir()
    (directory / "config.toml").write_text("invalid")
    with pytest.raises(SystemExit) as error:
        create_parser(catalog()).parse_args(["group", "settings", "--help"])
    assert error.value.code == 0


def test_environment_and_typed_overrides(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('[pipeline]\nabc = "file"\nkeep = true\n')
    settings = load_configuration(
        user_directory=tmp_path,
        start_directory=tmp_path,
        environment={
            "UNRELATED": "ignored",
            "OLD_VALUE": "legacy",
            "PROVIUM_CONFIG__PIPELINE__ABC": "environment",
            "PROVIUM_CONFIG__PIPELINE__COUNT": "2",
        },
        environment_aliases={"pipeline.abc": "OLD_VALUE", "pipeline.absent": "MISSING"},
        overrides=[
            "pipeline.abc=xyz",
            "pipeline.list=[1, 2]",
            'pipeline.text="3"',
            "empty=",
        ],
    )
    assert settings == {
        "pipeline": {
            "abc": "xyz",
            "keep": True,
            "count": 2,
            "list": [1, 2],
            "text": "3",
        },
        "empty": "",
    }


def test_nested_override_replaces_scalar(tmp_path: Path) -> None:
    assert load_configuration(
        user_directory=tmp_path,
        start_directory=tmp_path,
        environment={},
        overrides=["a=1", "a.b=2", "a.b=3"],
    ) == {"a": {"b": 3}}
