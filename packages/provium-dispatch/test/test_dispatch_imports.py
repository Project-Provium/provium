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
        "provium_dispatch.persistence.interface",
        "provium_dispatch.persistence",
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
assert "provium_dispatch.persistence.backends.postgres" not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path = {sys.path!r}\n" + code],
        check=True,
    )


def test_public_exports_remain_available():
    import provium_pipeline

    from provium_dispatch.persistence.backends.postgres import PostgresBackend
    from provium_dispatch.persistence.interface import PersistenceBackend, QueueBackend

    assert (
        PostgresBackend.__module__ == "provium_dispatch.persistence.backends.postgres"
    )
    for name in ("PostgresBackend", "PersistenceBackend", "Backend", "open_backend"):
        assert name not in provium_pipeline.__all__
        with pytest.raises(AttributeError):
            getattr(provium_pipeline, name)
    assert QueueBackend in PersistenceBackend.__mro__
    with pytest.raises(AttributeError):
        getattr(provium_pipeline, "missing_export")


def test_backend_enum_selection(monkeypatch):
    from provium_dispatch.persistence import Backend, open_backend
    from provium_dispatch.persistence.backends import postgres

    sentinel = object()
    connections = []

    def connect(connection):
        connections.append(connection)
        return sentinel

    monkeypatch.setattr(postgres, "PostgresBackend", connect)
    assert open_backend("explicit", backend=Backend.Postgres) is sentinel
    assert open_backend("default") is sentinel
    assert connections == ["explicit", "default"]
    with pytest.raises(TypeError, match="Backend"):
        open_backend("unused", backend="postgres")


def test_backend_selection_is_lazy():
    code = """
import sys
from provium_dispatch.persistence import open_backend
try:
    open_backend("unused", backend="unknown")
except TypeError as error:
    assert "Backend" in str(error)
else:
    raise AssertionError("unknown backend accepted")
assert "psycopg" not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path = {sys.path!r}\n" + code],
        check=True,
    )
