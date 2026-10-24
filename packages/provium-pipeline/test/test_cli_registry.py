"""Pipeline commands use the core Provium registry and dispatcher."""

import importlib.util
import tomllib
from pathlib import Path

import pytest
from provium.cli import CommandGroup, discover_command_catalogs, run

from provium_pipeline import cli


def test_pipeline_uses_core_registry(capsys):
    catalog = discover_command_catalogs()
    assert catalog.commands["pipeline"] is cli.PipelineCommand
    assert issubclass(cli.PipelineCommand, CommandGroup)
    assert set(cli.PipelineCommand.catalog.commands) == {
        "cache",
        "run",
        "status",
        "validate",
        "inspect",
        "lookup",
    }
    assert "artifact" in catalog.commands
    assert "execute" in catalog.commands
    with pytest.raises(SystemExit) as result:
        run(["pipeline", "--help"])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    assert "provium pipeline" in help_text
    for action in (
        "run",
        "status",
        "validate",
        "inspect",
        "lookup",
    ):
        assert action in help_text


@pytest.mark.parametrize("action", ["validate", "inspect"])
def test_inspection_does_not_load_runtime_or_create_store(tmp_path, action):
    import subprocess
    import sys

    path = tmp_path / "pipeline.yaml"
    path.write_text(
        "kind: PipelineConfiguration\nname: empty\ninputs: {}\nnodes: {}\noutputs: {}\n"
    )
    store = tmp_path / "artifacts"
    code = f"""
import sys
sys.path = {sys.path!r}
from provium.cli import run
assert run(["pipeline", {action!r}, "-f", {str(path)!r},
            "--store", {str(store)!r}]) == 0
for module in ("provium_pipeline.runtime", "provium_pipeline.workers",
               "provium_pipeline.store", "psycopg", "boto3"):
    assert module not in sys.modules, module
"""
    subprocess.run([sys.executable, "-c", code], check=True)
    assert not store.exists()


def test_no_standalone_pipeline_runner():
    assert not hasattr(cli, "run")
    assert not hasattr(cli, "main")
    assert importlib.util.find_spec("provium_pipeline.__main__") is None
    assert importlib.util.find_spec("provium_pipeline.cli.application") is None


def test_distribution_registers_only_core_cli_plugin():
    path = Path(__file__).parents[1] / "pyproject.toml"
    project = tomllib.loads(path.read_text())["project"]
    assert not project.get("scripts")
    assert project["entry-points"]["provium.cli.command_catalogs"] == {
        "pipeline": "provium_pipeline.cli:plugin",
    }


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--help"], 0),
        (["pipeline", "--help"], 0),
        (["pipeline", "run", "--help"], 0),
        (["pipeline", "missing"], 2),
        (["pipeline", "run"], 2),
        (["dispatch", "register", "--database", "unused"], 2),
        (["pipeline", "run", "saved", "-f", "unused", "--database", "unused"], 2),
        (["pipeline", "run", "-f", "unused", "--workers", "broken"], 2),
        (["pipeline", "run", "-f", "unused", "--idle-wait", "nan"], 2),
    ],
)
def test_help_and_invalid_arguments_do_not_load_execution(arguments, expected):
    import subprocess
    import sys

    code = f"""
import sys
sys.path = {sys.path!r}
import importlib.abc

blocked = []

class BlockExecution(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {{
            "provium_pipeline.preparation",
            "provium_pipeline.runtime",
            "provium_pipeline.workers",
            "provium_pipeline.store",
            "psycopg", "boto3",
        }}:
            blocked.append(fullname)
            raise AssertionError("premature execution import: " + fullname)

sys.meta_path.insert(0, BlockExecution())
from provium.cli import run
try:
    result = run({arguments!r})
except SystemExit as error:
    result = error.code
assert result == {expected}
assert not blocked, blocked
"""
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize(
    ("document", "options"),
    [
        ("kind: PipelineConfiguration\nname: bad\nnodes: {}\noutputs: {}\n", []),
        (
            "kind: PipelineConfiguration\nname: bad\ninputs: {}\n"
            "nodes: {}\noutputs: {bad: '$inputs.missing'}\n",
            [],
        ),
        (
            "kind: PipelineConfiguration\nname: bad\ninputs: {}\n"
            "nodes: {}\noutputs: {}\n",
            ["--only", "nodes.missing"],
        ),
        (
            "kind: PipelineConfiguration\nname: bad\ninputs: {}\n"
            "nodes: {}\noutputs: {}\n",
            ["--workers", "nodes.missing=2"],
        ),
    ],
)
@pytest.mark.parametrize("command", ["run", "status", "validate", "inspect"])
def test_invalid_pipeline_semantics_do_not_load_runtime(
    tmp_path, document, options, command
):
    import subprocess
    import sys

    path = tmp_path / "pipeline.yaml"
    path.write_text(document)
    arguments = ["pipeline", command, "-f", str(path), *options]
    code = f"""
import sys
sys.path = {sys.path!r}
import importlib.abc
blocked = []

class BlockRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {{
            "provium_pipeline.runtime", "provium_pipeline.workers",
            "provium_pipeline.store", "psycopg", "boto3",
        }}:
            blocked.append(fullname)
            raise AssertionError("premature runtime import: " + fullname)

sys.meta_path.insert(0, BlockRuntime())
from provium.cli import run
assert run({arguments!r}) == 2
assert not blocked, blocked
"""
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize("name", sorted(cli.PipelineCommand.catalog.commands))
def test_commands_own_configuration_and_execution(name):
    command_type = cli.PipelineCommand.catalog.commands[name]
    module_name = name.replace("-", "_")
    assert command_type.__module__ == f"provium_pipeline.cli.commands.{module_name}"
    if issubclass(command_type, CommandGroup):
        assert command_type.catalog.commands
    else:
        assert "configure" in command_type.__dict__
        assert "execute" in command_type.__dict__


def test_command_can_be_registered_under_another_name(tmp_path):
    from provium.cli import CommandCatalog

    path = tmp_path / "pipeline.yaml"
    path.write_text(
        "kind: PipelineConfiguration\nname: empty\ninputs: {}\nnodes: {}\noutputs: {}\n"
    )
    original = cli.PipelineCommand.catalog.commands["validate"]

    class CheckCommand(original):
        name = "check"

    catalog = CommandCatalog()
    catalog.register(CheckCommand)
    assert run(["check", "-f", str(path)], catalog=catalog) == 0
