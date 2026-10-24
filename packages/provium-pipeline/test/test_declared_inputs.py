import pytest
from provium_text_pipeline_example.artifact import DOCUMENT, TOKENS

from provium_pipeline.models import PipelineConfiguration, PipelineDefinition
from provium_pipeline.planning import build_plan


def graph(**changes):
    return PipelineConfiguration.model_validate(
        {
            "name": "pass-through",
            "inputs": {"document": DOCUMENT.identifier},
            "nodes": {},
            "outputs": {"document": "$inputs.document"},
            **changes,
        }
    )


def test_declared_inputs_support_typed_pass_through():
    plan = build_plan(graph())
    assert plan.input_types == {"document": DOCUMENT.identifier}
    assert plan.configuration.model_dump()["inputs"] == plan.input_types


def test_inputs_are_required_even_when_empty():
    with pytest.raises(ValueError, match="inputs"):
        PipelineConfiguration(name="empty", nodes={}, outputs={})
    assert build_plan(graph(inputs={}, outputs={})).input_types == {}


@pytest.mark.parametrize(
    "inputs", [{"bad.name": "type"}, {"document": ""}, {"document": " "}]
)
def test_invalid_input_declarations(inputs):
    with pytest.raises(ValueError):
        graph(inputs=inputs)


def test_undeclared_pass_through_is_rejected():
    with pytest.raises(ValueError, match="unknown pipeline output source"):
        build_plan(graph(outputs={"document": "$inputs.missing"}))


def test_definition_must_match_declared_names_and_types():
    with pytest.raises(ValueError, match="input names"):
        build_plan(graph(), PipelineDefinition(inputs={}, outputs={}))
    with pytest.raises(ValueError, match="incompatible pipeline input"):
        build_plan(
            graph(),
            PipelineDefinition(
                inputs={"document": TOKENS}, outputs={"document": DOCUMENT}
            ),
        )
    assert build_plan(
        graph(),
        PipelineDefinition(
            inputs={"document": DOCUMENT}, outputs={"document": DOCUMENT}
        ),
    ).input_types == {"document": DOCUMENT.identifier}


def test_node_bindings_require_declared_compatible_inputs():
    from test_runtime import configuration

    original = configuration()
    for inputs in ({}, {"document": TOKENS.identifier}):
        with pytest.raises(ValueError, match="incompatible or unknown source"):
            build_plan(original.model_copy(update={"inputs": inputs}))


@pytest.mark.parametrize("names", [[], ["document", "extra"], ["document"]])
def test_local_execution_enforces_declared_inputs(tmp_path, names):
    from test_store import make_artifact

    from provium_pipeline import ExecutionContext, InputSet
    from provium_pipeline.store import ArtifactStore

    source = str(make_artifact(tmp_path / "source.pa"))
    expected = TOKENS.identifier if names == ["document"] else DOCUMENT.identifier
    with ExecutionContext(
        graph(inputs={"document": expected}), ArtifactStore(str(tmp_path / "store"))
    ) as execution:
        execution.start([InputSet(dict.fromkeys(names, source))])
    assert execution.error is None
    assert execution.outcomes[0].state == "failed"


def test_local_pass_through_completes_with_declared_type(tmp_path):
    from test_store import make_artifact

    from provium_pipeline import ExecutionContext, InputSet
    from provium_pipeline.store import ArtifactStore

    source = str(make_artifact(tmp_path / "source.pa"))
    with ExecutionContext(graph(), ArtifactStore(str(tmp_path / "store"))) as execution:
        execution.start([InputSet({"document": source})])
    assert execution.outcomes[0].state == "completed"
    assert (
        execution.outcomes[0].outputs["document"].fingerprint["type"]
        == DOCUMENT.identifier
    )
