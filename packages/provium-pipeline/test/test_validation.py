"""Reject invalid pipelines before procedure side effects."""

from copy import deepcopy

import pytest
from provium import (
    ArtifactDefinition,
    ProcedureContract,
    ProcedureDefinition,
    ProcedureInputs,
    input,
)
from test_execution import DEFINITIONS, definition

from provium_pipeline import Pipeline, PipelineExecutor


@pytest.mark.parametrize(
    "patch",
    [
        {"version": True},
        {"version": 2},
        {"name": "../unsafe"},
        {"steps": {}},
        {"unknown": 1},
        {"inputs": ["a", "a"]},
        {"inputs": ["unused"]},
        {"outputs": {"value": "$inputs.a"}},
        {"outputs": {"value": "literal"}},
        {
            "outputs": {
                "a": "$steps.source.outputs.value",
                "b": "$steps.source.outputs.value",
            }
        },
    ],
)
def test_invalid_definitions(patch):
    data = definition() | patch
    with pytest.raises((ValueError, TypeError)):
        PipelineExecutor(procedures=DEFINITIONS).plan(Pipeline.from_mapping(data))


@pytest.mark.parametrize(
    "patch",
    [
        {"procedure": "missing"},
        {"inputs": {}},
        {"inputs": {"extra": "$steps.source.outputs.value"}},
        {"inputs": {"required": ["$steps.source.outputs.value"]}},
        {"setup_inputs": {}},
        {"configuration": {"prefix": "$config.missing"}},
        {"configuration": {"prefix": "$steps.source.outputs.value"}},
    ],
)
def test_invalid_steps(patch):
    data = definition()
    data["steps"]["transform"].update(patch)
    with pytest.raises((ValueError, TypeError)):
        PipelineExecutor(procedures=DEFINITIONS).plan(Pipeline.from_mapping(data))


@pytest.mark.parametrize(
    "value", ["$steps.source.outputs.value", [], ["$steps.source.outputs.value"] * 5]
)
def test_repeated_input_cardinality(value):
    data = definition()
    data["steps"]["transform"]["inputs"]["repeated"] = value
    with pytest.raises(ValueError, match="cardinality"):
        PipelineExecutor(procedures=DEFINITIONS).plan(Pipeline.from_mapping(data))


def test_nested_configuration_and_literal_escape():
    data = definition()
    data["configuration"] = {"nested": {"text": "hello"}}
    data["steps"]["source"]["configuration"] = {"text": "$config.nested.text"}
    data["steps"]["transform"]["configuration"] = {"prefix": "$$literal"}
    original = deepcopy(data)
    plan = PipelineExecutor(procedures=DEFINITIONS).plan(Pipeline.from_mapping(data))
    assert plan.steps[0].configuration["text"] == "hello"
    assert plan.steps[1].configuration["prefix"] == "$literal"
    assert data == original


OTHER_ARTIFACT = ArtifactDefinition(
    "test.OtherTextV1", "support.provium_test_pipeline.artifacts:TextArtifact", "Other"
)


class OtherContract(ProcedureContract[None]):
    class Inputs(ProcedureInputs):
        value = input(OTHER_ARTIFACT)


OTHER_PROCEDURE = ProcedureDefinition(
    "test.OtherV1", "test_validation:OtherProcedure", "Other", None, OtherContract
)


@pytest.mark.parametrize("external", [False, True])
def test_incompatible_connections(external):
    data = definition()
    reference = "$steps.source.outputs.value"
    if external:
        data["inputs"] = ["shared"]
        reference = "$inputs.shared"
        data["steps"]["transform"]["inputs"]["required"] = reference
    data["steps"]["other"] = {
        "procedure": OTHER_PROCEDURE.identifier,
        "inputs": {"value": reference},
    }
    definitions = DEFINITIONS | {OTHER_PROCEDURE.identifier: OTHER_PROCEDURE}
    with pytest.raises(ValueError, match="incompatible"):
        PipelineExecutor(procedures=definitions).plan(Pipeline.from_mapping(data))


def test_unconfigured_procedure_rejects_configuration():
    data = definition()
    data["steps"]["other"] = {
        "procedure": OTHER_PROCEDURE.identifier,
        "configuration": {"unexpected": 1},
        "inputs": {"value": "$steps.source.outputs.value"},
    }
    with pytest.raises(ValueError, match="does not accept configuration"):
        PipelineExecutor(
            procedures=DEFINITIONS | {OTHER_PROCEDURE.identifier: OTHER_PROCEDURE}
        ).plan(Pipeline.from_mapping(data))
