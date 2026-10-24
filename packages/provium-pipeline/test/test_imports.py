"""Import boundaries must work without optional backend drivers."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "provium_pipeline",
        "provium_pipeline.models",
        "provium_pipeline.runtime",
        "provium_pipeline.coordination",
        "provium_pipeline.cli",
        "provium_pipeline.cli.commands.pipeline",
    ],
)
def test_import_without_backend_drivers(module):
    code = f"""
import importlib
import importlib.abc
import sys

class BlockDrivers(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {{"psycopg", "boto3"}}:
            raise AssertionError("unexpected driver import: " + fullname)

sys.meta_path.insert(0, BlockDrivers())
importlib.import_module({module!r})
assert "provium_pipeline.persistence.backends.postgres" not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path = {sys.path!r}\n" + code],
        check=True,
    )


def test_public_exports_remain_available():
    import provium_pipeline

    for name in (
        "ComputeProfile",
        "PostgresBackend",
        "PersistenceBackend",
        "Backend",
        "open_backend",
    ):
        assert name not in provium_pipeline.__all__
        with pytest.raises(AttributeError):
            getattr(provium_pipeline, name)


def test_removed_compatibility_modules():
    import importlib.util

    for name in ("database", "cli.definitions", "cli.preparation"):
        assert importlib.util.find_spec("provium_pipeline." + name) is None


def test_preparation_is_independent_of_cli():
    code = """
import sys
from provium_pipeline.preparation import prepare_pipeline
from provium_pipeline.definitions import load_definition
assert callable(load_definition)
try:
    prepare_pipeline()
except ValueError:
    pass
else:
    raise AssertionError("missing pipeline accepted")
assert "provium_pipeline.cli" not in sys.modules
assert "psycopg" not in sys.modules
assert "provium_pipeline.runtime" not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path = {sys.path!r}\n" + code],
        check=True,
    )
