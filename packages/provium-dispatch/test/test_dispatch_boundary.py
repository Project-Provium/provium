"""The basic pipeline must work without installing dispatch."""

import subprocess
import sys

import pytest


def test_dispatch_owns_input_set_commands(capsys):
    from provium.cli import discover_command_catalogs, run

    catalog = discover_command_catalogs()
    dispatch = catalog.commands["dispatch"]
    assert set(dispatch.catalog.commands) == {
        "cache",
        "run",
        "status",
        "inspect",
        "validate",
        "input-set",
        "lookup",
        "register",
        "unregister",
        "enqueue",
        "retry",
        "list",
        "queue",
    }
    assert "input-set" not in catalog.commands["pipeline"].catalog.commands
    with pytest.raises(SystemExit) as result:
        run(["dispatch", "--help"])
    assert result.value.code == 0
    assert "input-set" in capsys.readouterr().out


def test_pipeline_imports_do_not_depend_on_dispatch():
    code = """
import importlib.abc
import sys

class BlockDispatch(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"provium_dispatch", "sqlite3", "psycopg"}:
            raise AssertionError("unexpected persistence import: " + fullname)

sys.meta_path.insert(0, BlockDispatch())
from provium_pipeline import ExecutionContext
from provium_pipeline.cli import PipelineCommand
from provium_pipeline.input_sets import input_set_hash
assert "input-set" not in PipelineCommand.catalog.commands
assert input_set_hash({"video": {"fingerprint": {"content": "a"}}})
"""
    subprocess.run([sys.executable, "-c", code], check=True)
