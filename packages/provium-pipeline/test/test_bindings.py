"""External bindings, isolation, and cancellation behavior."""

from pathlib import Path

import pytest
from provium import (
    ArtifactReadBinding,
    ArtifactWriteBinding,
    ProcedureExecutor,
    session,
)
from support.provium_test_pipeline.artifacts import TextArtifact
from support.provium_test_pipeline.contracts import SOURCE_PROCEDURE
from test_execution import DEFINITIONS, bindings, definition

from provium_pipeline import Pipeline, PipelineExecutor


def external_pipeline(tmp_path):
    inputs = {}
    for name in ("a", "b"):
        path = tmp_path / f"{name}.pa"
        ProcedureExecutor().execute(
            SOURCE_PROCEDURE,
            configuration_layers=[{"text": name.upper()}],
            outputs={"value": ArtifactWriteBinding(TextArtifact, path)},
        )
        inputs[name] = ArtifactReadBinding(TextArtifact, path)
    data = definition()
    del data["steps"]["source"]
    data["inputs"] = ["a", "b"]
    data["steps"]["transform"]["setup_inputs"] = {"setup": "$inputs.a"}
    data["steps"]["transform"]["inputs"] = {
        "required": "$inputs.b",
        "repeated": ["$inputs.a", "$inputs.b"],
    }
    return Pipeline.from_mapping(data), inputs


def test_external_inputs_and_repeated_input_order(tmp_path):
    pipeline, inputs = external_pipeline(tmp_path)
    PipelineExecutor(procedures=DEFINITIONS).execute(
        pipeline, inputs=inputs, outputs=bindings(tmp_path)
    )
    with (
        session(),
        ArtifactReadBinding(TextArtifact, tmp_path / "text.pa").open() as reader,
    ):
        assert reader.read_text() == "<ABAB>"


@pytest.mark.parametrize(
    "case",
    [
        "missing-input",
        "missing-output",
        "duplicate-output",
        "overlap",
        "directory",
        "missing-file",
        "wrong-input-direction",
        "wrong-output-direction",
    ],
)
def test_bad_bindings(tmp_path, case):
    pipeline, inputs = external_pipeline(tmp_path)
    outputs = bindings(tmp_path)
    if case == "missing-input":
        inputs.pop("a")
    elif case == "missing-output":
        outputs.pop("text")
    elif case == "duplicate-output":
        outputs["report"] = outputs["text"]
    elif case == "overlap":
        outputs["text"] = ArtifactWriteBinding(TextArtifact, inputs["a"].path)
    elif case == "directory":
        outputs["text"] = ArtifactWriteBinding(TextArtifact, tmp_path)
    elif case == "missing-file":
        inputs["a"] = ArtifactReadBinding(TextArtifact, tmp_path / "missing")
    elif case == "wrong-input-direction":
        inputs["a"] = ArtifactWriteBinding(TextArtifact, inputs["a"].path)
    else:
        outputs["text"] = inputs["a"]
    with pytest.raises((ValueError, TypeError)):
        PipelineExecutor(procedures=DEFINITIONS).execute(
            pipeline, inputs=inputs, outputs=outputs
        )
    assert not (tmp_path / "report.pa").exists()


def test_retained_runs_are_isolated(tmp_path):
    executor = PipelineExecutor(procedures=DEFINITIONS)
    pipeline = Pipeline.from_mapping(definition())
    results = [
        executor.execute(
            pipeline, outputs=bindings(tmp_path), keep_intermediates=tmp_path / "debug"
        )
        for _ in range(2)
    ]
    assert results[0].identity != results[1].identity
    assert results[0].intermediate_directory != results[1].intermediate_directory
    assert all(result.intermediate_directory.is_dir() for result in results)


def test_cancellation_cleans_up_after_a_step(tmp_path, monkeypatch):
    import provium_pipeline.executor as module

    paths = []
    original = module.TemporaryDirectory

    def temporary(*args, **kwargs):
        directory = original(*args, **kwargs)
        paths.append(Path(directory.name))
        return directory

    monkeypatch.setattr(module, "TemporaryDirectory", temporary)

    class Cancelled(RuntimeError):
        pass

    class Token:
        def raise_if_cancelled(self):
            if paths and any(paths[0].iterdir()):
                raise Cancelled("cancelled")

    with pytest.raises(Cancelled):
        module.PipelineExecutor(procedures=DEFINITIONS).execute(
            Pipeline.from_mapping(definition()),
            outputs=bindings(tmp_path),
            cancellation=Token(),
        )
    assert paths and all(not path.exists() for path in paths)
    assert not (tmp_path / "text.pa").exists()
