"""Preparation is usable with plain Python arguments and injected persistence."""

from unittest.mock import Mock

import pytest

from provium_pipeline.preparation import load_pipeline, prepare_pipeline


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
