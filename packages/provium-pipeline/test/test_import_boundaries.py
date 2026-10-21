"""Fresh-process checks for imports unrelated to the requested interface."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("module", "excluded"),
    [
        (
            "provium_pipeline.persistence.interface",
            ["provium_pipeline.models", "provium"],
        ),
        (
            "provium_pipeline.persistence.factory",
            ["provium_pipeline.models", "provium"],
        ),
        ("provium_pipeline.models", ["provium", "yaml"]),
        ("provium_pipeline.definitions", ["provium", "yaml"]),
        (
            "provium_pipeline.preparation",
            ["provium_pipeline.persistence", "provium", "yaml"],
        ),
        ("provium_pipeline.runtime", ["provium_pipeline.workers", "multiprocessing"]),
        (
            "provium_pipeline.persistence.backends.postgres",
            ["provium_pipeline.planning"],
        ),
    ],
)
def test_module_imports_only_its_dependencies(module, excluded):
    code = f"""
import sys
sys.path = {sys.path!r}
import importlib
importlib.import_module({module!r})
for name in {excluded!r}:
    assert name not in sys.modules, (name, {module!r})
"""
    subprocess.run([sys.executable, "-c", code], check=True)
