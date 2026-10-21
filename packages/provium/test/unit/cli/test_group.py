import argparse

import pytest

from provium.cli import Command, CommandCatalog, CommandGroup, create_parser, run


class EchoCommand(Command):
    name = "echo"
    help = "Echo a value"

    def configure(self, parser):
        parser.add_argument("command")
        parser.add_argument("--code", type=int, default=7)

    def execute(self, arguments):
        print(arguments.command)
        return arguments.code


def nested_catalog(depth):
    catalog = CommandCatalog()
    catalog.register(EchoCommand)
    for _ in range(depth):
        children = catalog

        class Group(CommandGroup):
            name = "group"
            help = "Nested commands"
            catalog = children

        catalog = CommandCatalog()
        catalog.register(Group)
    return catalog


@pytest.mark.parametrize("depth", [1, 3, 8])
def test_arbitrary_nesting_dispatches_leaf(depth, capsys):
    catalog = nested_catalog(depth)
    arguments = ["group"] * depth + ["echo", "hello", "--code", "9"]
    parsed = create_parser(catalog).parse_args(arguments)
    assert isinstance(parsed._provium_command, EchoCommand)
    assert parsed.command_name == "group"
    assert run(arguments, catalog=catalog) == 9
    assert capsys.readouterr().out == "hello\n"


@pytest.mark.parametrize("depth", [0, 1, 2])
def test_help_at_each_group_level(depth, capsys):
    with pytest.raises(SystemExit) as result:
        run(["group"] * depth + ["--help"], catalog=nested_catalog(2))
    assert result.value.code == 0
    output = capsys.readouterr().out
    assert "provium" + " group" * depth in output
    assert ("echo" if depth == 2 else "group") in output


@pytest.mark.parametrize(
    "arguments",
    [[], ["group"], ["group", "group"], ["group", "missing"]],
)
def test_nested_groups_require_a_valid_leaf(arguments):
    with pytest.raises(SystemExit) as result:
        run(arguments, catalog=nested_catalog(2))
    assert result.value.code == 2


def test_group_can_be_configured_and_executed_directly(capsys):
    group = nested_catalog(2).resolve("group")()
    parser = argparse.ArgumentParser()
    group.configure(parser)
    assert group.execute(parser.parse_args(["group", "echo", "direct"])) == 7
    assert capsys.readouterr().out == "direct\n"


def test_sibling_groups_keep_their_own_catalogs(capsys):
    class OtherEcho(EchoCommand):
        def execute(self, arguments):
            print("other")
            return 3

    class OtherGroup(CommandGroup):
        name = "other"
        help = "Other commands"
        catalog = CommandCatalog()

    OtherGroup.catalog.register(OtherEcho)
    catalog = nested_catalog(1)
    catalog.register(OtherGroup)
    assert run(["group", "echo", "first"], catalog=catalog) == 7
    assert run(["other", "echo", "second"], catalog=catalog) == 3
    assert capsys.readouterr().out == "first\nother\n"


def test_group_respects_leaf_help_configuration():
    class HelpArgument(EchoCommand):
        add_help = False

        def configure(self, parser):
            parser.add_argument("--help", dest="command", required=True)
            parser.set_defaults(code=4)

    class Group(CommandGroup):
        name = "group"
        help = "Commands"
        catalog = CommandCatalog()

    Group.catalog.register(HelpArgument)
    catalog = CommandCatalog()
    catalog.register(Group)
    assert run(["group", "echo", "--help", "value"], catalog=catalog) == 4


def group_with(*commands):
    class Group(CommandGroup):
        name = "group"
        help = "Shared group"
        catalog = CommandCatalog()

    for command in commands:
        Group.catalog.register(command)
    return Group


class ExtraCommand(EchoCommand):
    name = "extra"


def test_shared_paths_merge_recursively_without_mutating_sources(capsys):
    first = group_with(group_with(EchoCommand))
    second = group_with(group_with(ExtraCommand))
    catalog = CommandCatalog()
    catalog.register(first)
    assert catalog.register(second) is second
    assert run(["group", "group", "echo", "first"], catalog=catalog) == 7
    assert run(["group", "group", "extra", "second"], catalog=catalog) == 7
    assert capsys.readouterr().out == "first\nsecond\n"
    assert set(first.catalog.resolve("group").catalog.commands) == {"echo"}
    assert set(second.catalog.resolve("group").catalog.commands) == {"extra"}


def test_failed_merge_does_not_publish_partial_changes():
    first = group_with(group_with(EchoCommand))
    second = group_with(group_with(ExtraCommand, EchoCommand))
    catalog = CommandCatalog()
    catalog.register(first)
    with pytest.raises(ValueError, match="already registered"):
        catalog.register(second)
    assert catalog.resolve("group") is first
    assert set(first.catalog.resolve("group").catalog.commands) == {"echo"}


@pytest.mark.parametrize("group_first", [False, True])
def test_group_and_leaf_cannot_share_the_same_path(group_first):
    class Leaf(EchoCommand):
        name = "group"

    group = group_with(EchoCommand)
    entries = (group, Leaf) if group_first else (Leaf, group)
    catalog = CommandCatalog()
    catalog.register(entries[0])
    with pytest.raises(ValueError, match="already registered"):
        catalog.register(entries[1])


def test_packages_contribute_to_shared_group_paths(monkeypatch, capsys):
    from types import SimpleNamespace

    from provium.cli import (
        CLI_PLUGIN_API_VERSION,
        CLIPlugin,
        discover_command_catalogs,
        reset_command_discovery,
    )

    first = CommandCatalog()
    first.register(group_with(group_with(EchoCommand)))
    second = CommandCatalog()
    second.register(group_with(group_with(ExtraCommand)))
    plugins = [
        CLIPlugin(CLI_PLUGIN_API_VERSION, "one", first),
        CLIPlugin(CLI_PLUGIN_API_VERSION, "two", second),
    ]
    entries = [
        SimpleNamespace(name="plugin", load=lambda plugin=plugin: plugin)
        for plugin in plugins
    ]
    monkeypatch.setattr(
        "provium.cli.discovery.metadata.entry_points", lambda **kwargs: entries
    )
    reset_command_discovery()
    try:
        for _ in range(2):
            assert run(["group", "group", "echo", "one"]) == 7
            assert run(["group", "group", "extra", "two"]) == 7
            assert "artifact" in discover_command_catalogs().commands
            reset_command_discovery()
        assert capsys.readouterr().out == "one\ntwo\none\ntwo\n"
    finally:
        reset_command_discovery()
