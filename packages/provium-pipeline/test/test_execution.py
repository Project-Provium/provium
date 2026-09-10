"""End-to-end checks using core's shared procedure fixtures."""

from pathlib import Path

import pytest
from provium import ArtifactReadBinding, ArtifactWriteBinding, session
from support.provium_test_pipeline.artifacts import TextArtifact
from support.provium_test_pipeline.contracts import (
    FAILING_PROCEDURE,
    SOURCE_PROCEDURE,
    TRANSFORM_PROCEDURE,
)

from provium_pipeline import Pipeline, PipelineExecutor

DEFINITIONS = {
    item.identifier: item
    for item in (SOURCE_PROCEDURE, TRANSFORM_PROCEDURE, FAILING_PROCEDURE)
}


def definition():
    return {
        "version": 1,
        "name": "text",
        "configuration": {"text": "A"},
        "inputs": [],
        "steps": {
            "transform": {
                "procedure": TRANSFORM_PROCEDURE.identifier,
                "setup_inputs": {"setup": "$steps.source.outputs.value"},
                "inputs": {
                    "required": "$steps.source.outputs.value",
                    "repeated": ["$steps.source.outputs.value"],
                },
                "configuration": {"prefix": "<", "suffix": ">"},
            },
            "source": {
                "procedure": SOURCE_PROCEDURE.identifier,
                "configuration": {"text": "$config.text"},
            },
        },
        "outputs": {
            "text": "$steps.transform.outputs.transformed",
            "report": "$steps.transform.outputs.summary",
        },
    }


def bindings(tmp_path):
    return {
        name: ArtifactWriteBinding(TextArtifact, tmp_path / f"{name}.pa")
        for name in ("text", "report")
    }


def test_execution(tmp_path):
    pipeline = Pipeline.from_mapping(definition())
    executor = PipelineExecutor(procedures=DEFINITIONS)
    assert [step.name for step in executor.plan(pipeline).steps] == [
        "source",
        "transform",
    ]
    outputs = bindings(tmp_path)
    result = executor.execute(
        pipeline, outputs=outputs, configuration_layers=[{"text": "B"}]
    )
    assert set(result.outputs) == {"text", "report"}
    assert list(result.steps) == ["source", "transform"]
    assert result.intermediate_directory is None
    assert sorted(path.name for path in tmp_path.iterdir()) == ["report.pa", "text.pa"]
    with (
        session(),
        ArtifactReadBinding(TextArtifact, outputs["text"].path).open() as reader,
    ):
        assert reader.read_text() == "<BBB>"
        assert len(reader.metadata.lineage.executions) == 2


def test_yaml_and_retention(tmp_path):
    import yaml

    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(definition()))
    result = PipelineExecutor(procedures=DEFINITIONS).execute(
        Pipeline.from_yaml(path),
        outputs=bindings(tmp_path),
        keep_intermediates=tmp_path / "debug",
    )
    assert result.intermediate_directory.is_dir()
    assert list(result.intermediate_directory.rglob("*.pa"))


@pytest.mark.parametrize(
    "reference",
    [
        "$steps.missing.outputs.value",
        "$steps.source.outputs.missing",
        "$inputs.missing",
    ],
)
def test_invalid_references(tmp_path, reference):
    data = definition()
    data["steps"]["transform"]["inputs"]["required"] = reference
    with pytest.raises(ValueError):
        PipelineExecutor(procedures=DEFINITIONS).execute(
            Pipeline.from_mapping(data), outputs=bindings(tmp_path)
        )
    assert not list(tmp_path.iterdir())


def test_cycle_and_invalid_later_configuration(tmp_path):
    data = definition()
    data["steps"]["transform"]["inputs"]["required"] = (
        "$steps.transform.outputs.transformed"
    )
    executor = PipelineExecutor(procedures=DEFINITIONS)
    with pytest.raises(ValueError, match="cycle"):
        executor.plan(Pipeline.from_mapping(data))
    data = definition()
    data["steps"]["transform"]["configuration"] = {"unexpected": True}
    with pytest.raises((ValueError, TypeError)):
        executor.execute(Pipeline.from_mapping(data), outputs=bindings(tmp_path))
    assert not list(tmp_path.iterdir())


def test_failure_cleanup(tmp_path, monkeypatch):
    import provium_pipeline.executor as module

    directories = []
    original = module.TemporaryDirectory

    def temporary_directory(*args, **kwargs):
        directory = original(*args, **kwargs)
        directories.append(Path(directory.name))
        return directory

    monkeypatch.setattr(module, "TemporaryDirectory", temporary_directory)
    data = definition()
    data["steps"]["fail"] = {
        "procedure": FAILING_PROCEDURE.identifier,
        "inputs": {"source": "$steps.source.outputs.value"},
    }
    data["outputs"] = {"failed": "$steps.fail.outputs.result"}
    with pytest.raises(RuntimeError):
        PipelineExecutor(procedures=DEFINITIONS).execute(
            Pipeline.from_mapping(data),
            outputs={
                "failed": ArtifactWriteBinding(TextArtifact, tmp_path / "failed.pa")
            },
        )
    assert directories and all(not path.exists() for path in directories)
    assert not (tmp_path / "failed.pa").exists()
