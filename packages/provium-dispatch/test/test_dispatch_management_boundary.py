"""Durable management belongs to the dispatch plugin."""

import pytest
from provium.cli import discover_command_catalogs, run
from provium_pipeline.cli.commands.pipeline import PipelineCommand

MANAGEMENT = ("register", "unregister", "enqueue", "retry", "list", "queue")


@pytest.mark.parametrize("name", MANAGEMENT)
def test_management_is_owned_by_dispatch(name, capsys):
    catalog = discover_command_catalogs()
    dispatch = catalog.commands["dispatch"]
    assert name not in PipelineCommand.catalog.commands
    command = dispatch.catalog.commands[name]
    assert command.__module__ == f"provium_dispatch.cli.commands.{name}"
    with pytest.raises(SystemExit) as result:
        run(["dispatch", name, "--help"])
    assert result.value.code == 0
    assert f"provium dispatch {name}" in capsys.readouterr().out
