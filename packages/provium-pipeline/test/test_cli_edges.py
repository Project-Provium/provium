import os
import sys
from types import SimpleNamespace

import pytest

from provium_pipeline import PipelineDefinition, definitions
from provium_pipeline.cli.arguments import assignments
from provium_pipeline.definitions import load_definition

DSN = os.environ.get("PROVIUM_TEST_DATABASE")


@pytest.mark.parametrize("values", [["x"], ["=x"], ["x="], ["x=a", "x=b"]])
def test_invalid_input_assignments(values):
    with pytest.raises(ValueError):
        assignments(values)


def test_definition_loading(monkeypatch):
    definition = PipelineDefinition({}, {})
    monkeypatch.setitem(
        sys.modules, "application", SimpleNamespace(definition=definition, wrong=42)
    )
    assert load_definition("p", "application:definition") is definition
    with pytest.raises(ValueError):
        load_definition("p", "application")
    with pytest.raises(TypeError):
        load_definition("p", "application:wrong")
    entry = SimpleNamespace(load=lambda: definition)
    monkeypatch.setattr(definitions, "entry_points", lambda **kw: [entry])
    assert load_definition("p", None) is definition
    monkeypatch.setattr(definitions, "entry_points", lambda **kw: [entry, entry])
    with pytest.raises(ValueError, match="duplicate"):
        load_definition("p", None)
