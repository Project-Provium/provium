"""Preparation is usable with plain Python arguments and injected persistence."""

from unittest.mock import Mock

import pytest

from provium_dispatch.preparation import load_pipeline, prepare_pipeline


def test_prepare_local_files(tmp_path):
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        "kind: PipelineConfiguration\nname: empty\ninputs: {}\nnodes: {}\noutputs: {}\n"
        "---\nkind: ComputeProfile\nname: local\n"
    )
    result = prepare_pipeline(files=[str(path)], profile_name="local")
    assert result.plan.configuration.name == "empty"
    assert result.profile.name == "local"
    assert result.backend is None
    assert result.summary() == {"pipeline": "empty", "order": [], "settings": {}}
    assert load_pipeline(files=[str(path)]).name == "empty"


def test_prepare_with_injected_backend():
    backend = Mock()
    backend.get.return_value = {
        "kind": "PipelineConfiguration",
        "name": "saved",
        "inputs": {},
        "nodes": {},
        "outputs": {},
    }
    backend.definition.return_value = None
    result = prepare_pipeline(name="saved", backend=backend)
    assert result.backend is backend
    assert result.plan.configuration.name == "saved"
    backend.get.assert_called_once_with("PipelineConfiguration", "saved")
    backend.definition.assert_called_once_with("saved")


@pytest.mark.parametrize("prepare", [load_pipeline, prepare_pipeline])
def test_registered_pipeline_requires_explicit_backend(prepare):
    with pytest.raises(ValueError, match="persistence backend"):
        prepare(name="saved")


@pytest.mark.parametrize("prepare", [load_pipeline, prepare_pipeline])
def test_ambiguous_selection_rejected_before_backend_access(prepare, tmp_path):
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        "kind: PipelineConfiguration\nname: empty\ninputs: {}\nnodes: {}\noutputs: {}\n"
    )
    backend = Mock()
    with pytest.raises(ValueError, match="select files or a registered pipeline"):
        prepare(files=[str(path)], name="saved", backend=backend)
    backend.get.assert_not_called()


@pytest.mark.parametrize("prepare", [load_pipeline, prepare_pipeline])
def test_missing_graph_rejected(prepare):
    with pytest.raises(ValueError, match="select exactly one pipeline"):
        prepare()


@pytest.mark.parametrize("explicit", [False, True])
def test_registered_definition_snapshot_used(explicit, monkeypatch):
    from provium_pipeline import definitions

    from provium_dispatch import PipelineDefinition

    definition = PipelineDefinition({}, {}, frozenset({"local"}))
    backend = Mock()
    backend.get.side_effect = [
        {
            "kind": "PipelineConfiguration",
            "name": "saved",
            "inputs": {},
            "nodes": {},
            "outputs": {},
        },
        {"kind": "ComputeProfile", "name": "local"},
    ]
    backend.definition.return_value = definition
    monkeypatch.setattr(
        definitions, "load_definition", lambda *args: definition if explicit else None
    )
    result = prepare_pipeline(name="saved", backend=backend, profile_name="local")
    assert result.definition is definition
    assert result.profile.name == "local"


def test_registered_definition_mismatch_rejected(monkeypatch):
    from provium_pipeline import definitions

    from provium_dispatch import PipelineDefinition

    backend = Mock()
    backend.get.return_value = {
        "kind": "PipelineConfiguration",
        "name": "saved",
        "inputs": {},
        "nodes": {},
        "outputs": {},
    }
    backend.definition.return_value = PipelineDefinition({}, {}, frozenset({"local"}))
    monkeypatch.setattr(
        definitions, "load_definition", lambda *args: PipelineDefinition({}, {})
    )
    with pytest.raises(ValueError, match="definition differs from registered snapshot"):
        prepare_pipeline(name="saved", backend=backend)


def test_profile_allowlist_enforced(monkeypatch):
    from provium_pipeline import definitions

    from provium_dispatch import PipelineDefinition

    backend = Mock()
    backend.get.side_effect = [
        {
            "kind": "PipelineConfiguration",
            "name": "saved",
            "inputs": {},
            "nodes": {},
            "outputs": {},
        },
        {"kind": "ComputeProfile", "name": "forbidden"},
    ]
    backend.definition.return_value = PipelineDefinition({}, {}, frozenset({"local"}))
    monkeypatch.setattr(definitions, "load_definition", lambda *args: None)
    with pytest.raises(ValueError, match="profile not allowed: forbidden"):
        prepare_pipeline(name="saved", backend=backend, profile_name="forbidden")
