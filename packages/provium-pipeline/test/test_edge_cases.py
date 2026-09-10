"""Artifact type checks and structured configuration substitution."""

import pytest
from provium import (
    ArtifactReadBinding,
    ArtifactWriteBinding,
    ProcedureConfig,
    ProcedureContract,
    ProcedureDefinition,
)
from support.provium_test_pipeline.artifacts import TextArtifact
from test_bindings import external_pipeline
from test_execution import DEFINITIONS, bindings, definition

from provium_pipeline import Pipeline, PipelineExecutor


class OtherTextArtifact(TextArtifact):
    pass


@pytest.mark.parametrize("direction", ["input", "output"])
def test_rejects_incompatible_binding_artifact(tmp_path, direction):
    pipeline, inputs = external_pipeline(tmp_path)
    outputs = bindings(tmp_path)
    if direction == "input":
        inputs["a"] = ArtifactReadBinding(OtherTextArtifact, inputs["a"].path)
    else:
        outputs["text"] = ArtifactWriteBinding(OtherTextArtifact, tmp_path / "text.pa")
    with pytest.raises(TypeError, match="incompatible"):
        PipelineExecutor(procedures=DEFINITIONS).execute(
            pipeline, inputs=inputs, outputs=outputs
        )
    assert not (tmp_path / "text.pa").exists()


class PayloadConfig(ProcedureConfig):
    payload: dict[str, list[str]]


class PayloadContract(ProcedureContract[PayloadConfig]):
    configuration = PayloadConfig


PAYLOAD = ProcedureDefinition(
    "test.PayloadV1",
    "test_edge_cases:PayloadProcedure",
    "Payload",
    None,
    PayloadContract,
)


def test_nested_mapping_and_list_configuration():
    pipeline = Pipeline.from_mapping(
        {
            "name": "payload",
            "configuration": {"word": "hello"},
            "steps": {
                "payload": {
                    "procedure": PAYLOAD.identifier,
                    "configuration": {
                        "payload": {"items": ["$config.word", "literal"]}
                    },
                }
            },
        }
    )
    plan = PipelineExecutor(procedures={PAYLOAD.identifier: PAYLOAD}).plan(pipeline)
    assert plan.steps[0].configuration == {"payload": {"items": ["hello", "literal"]}}


def test_no_public_outputs(tmp_path):
    data = definition()
    data["outputs"] = {}
    result = PipelineExecutor(procedures=DEFINITIONS).execute(
        Pipeline.from_mapping(data)
    )
    assert not result.outputs
    assert set(result.steps) == {"source", "transform"}
    assert not list(tmp_path.iterdir())
