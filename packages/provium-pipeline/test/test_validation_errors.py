from types import SimpleNamespace

import pytest
from test_runtime import configuration

from provium_pipeline import (
    ComputeProfile,
    PipelineConfiguration,
    PipelineDefinition,
    load_documents,
)
from provium_pipeline.planning import build_plan


@pytest.mark.parametrize("name", ["", "with.dot", "with space", "a/b"])
def test_invalid_names(name):
    with pytest.raises(ValueError):
        ComputeProfile(name=name)


@pytest.mark.parametrize(
    "rules", [[{}], [{"default": 1, "tags.x": 2}], [{"nodes.x": 1}]]
)
def test_invalid_rule_structure(rules):
    with pytest.raises(ValueError):
        ComputeProfile(name="test", workers=rules)


@pytest.mark.parametrize("limit", [0, -1, True, float("inf"), float("nan")])
def test_invalid_time_limits(limit):
    with pytest.raises(ValueError):
        ComputeProfile(name="test", soft_time_limit=limit)


def test_null_limit_clears_and_other_pipeline_rules_are_ignored():
    profile = ComputeProfile(
        name="test",
        soft_time_limits=[
            {"default": 4},
            {"tags.x": None},
            {"pipelines.other.nodes.node": 1},
        ],
    )
    assert profile.settings("this", "node", ["x"]).soft is None


@pytest.mark.parametrize(
    "content, error",
    [
        ("- not-a-document", TypeError),
        ("kind: Unknown\nname: x", ValueError),
        (
            "kind: ComputeProfile\nname: x\n---\nkind: ComputeProfile\nname: x",
            ValueError,
        ),
    ],
)
def test_invalid_document_batches(tmp_path, content, error):
    path = tmp_path / "documents.yaml"
    path.write_text(content)
    with pytest.raises(error):
        load_documents([path])


def test_empty_documents_are_ignored(tmp_path):
    path = tmp_path / "documents.yaml"
    path.write_text("---\n---\n")
    assert load_documents([path]) == []


@pytest.mark.parametrize(
    "nodes, outputs",
    [
        (
            {"a": {"procedure": "x", "inputs": {"x": "$nodes.missing.o"}}},
            {"o": "$nodes.a.o"},
        ),
        ({}, {"o": "$nodes.missing.o"}),
        ({"a": {"procedure": "x"}}, {}),
    ],
)
def test_missing_and_unused_nodes(nodes, outputs):
    with pytest.raises(ValueError):
        PipelineConfiguration(name="test", nodes=nodes, outputs=outputs).order()


def test_unknown_fields_and_incompatible_contracts():
    graph = configuration()
    with pytest.raises(ValueError, match="no configuration"):
        build_plan(
            graph.model_copy(
                update={
                    "nodes": {
                        "tokenize": graph.nodes["tokenize"].model_copy(
                            update={"configuration": {"x": 1}}
                        )
                    }
                }
            )
        )
    for bindings in ({"unknown": "$inputs.document"}, {"source": []}, {}):
        with pytest.raises(ValueError):
            build_plan(
                graph.model_copy(
                    update={
                        "nodes": {
                            "tokenize": graph.nodes["tokenize"].model_copy(
                                update={"inputs": bindings}
                            )
                        }
                    }
                )
            )
    with pytest.raises(ValueError, match="incompatible"):
        build_plan(graph, PipelineDefinition({"document": "wrong"}, {}))
    with pytest.raises(ValueError, match="unknown pipeline output"):
        build_plan(
            graph.model_copy(update={"outputs": {"x": "$nodes.tokenize.missing"}})
        )


def test_pipeline_definition_output_validation():
    from provium_text_pipeline_example.artifact import DOCUMENT

    graph = configuration()
    with pytest.raises(ValueError, match="output names"):
        build_plan(graph, PipelineDefinition({"document": DOCUMENT}, {}))
    with pytest.raises(ValueError, match="incompatible pipeline output"):
        build_plan(
            graph, PipelineDefinition({"document": DOCUMENT}, {"tokens": DOCUMENT})
        )


def test_collection_cardinality_validation():
    from provium_text_pipeline_example.procedure.tokenize.definition import TOKENIZE

    field = SimpleNamespace(
        name="source",
        repeated=True,
        minimum=1,
        maximum=2,
        artifact_identifier="document",
    )
    contract = SimpleNamespace(
        configuration=None,
        metadata=SimpleNamespace(
            digest="contract", inputs=[field], setup_inputs=[], outputs=[]
        ),
    )
    procedure = SimpleNamespace(
        identifier=TOKENIZE.identifier, resolve_contract=lambda: contract
    )
    graph = configuration()
    with pytest.raises(ValueError, match="collection"):
        build_plan(graph, procedures={TOKENIZE.identifier: procedure})
    for refs in ([], ["$inputs.a", "$inputs.b", "$inputs.c"]):
        node = graph.nodes["tokenize"].model_copy(update={"inputs": {"source": refs}})
        with pytest.raises(ValueError, match="cardinality"):
            build_plan(
                graph.model_copy(update={"nodes": {"tokenize": node}}),
                procedures={TOKENIZE.identifier: procedure},
            )


def test_effective_configuration_defaults_have_identical_provenance():
    from provium.procedure.config import ProcedureConfig
    from provium_text_pipeline_example.procedure.tokenize.definition import TOKENIZE

    class Config(ProcedureConfig):
        suffix: str = "!"

    original = TOKENIZE.resolve_contract()
    contract = SimpleNamespace(configuration=Config, metadata=original.metadata)
    procedure = SimpleNamespace(
        identifier=TOKENIZE.identifier, resolve_contract=lambda: contract
    )
    graph = configuration()
    first = build_plan(graph, procedures={TOKENIZE.identifier: procedure})
    node = graph.nodes["tokenize"].model_copy(update={"configuration": {"suffix": "!"}})
    second = build_plan(
        graph.model_copy(update={"nodes": {"tokenize": node}}),
        procedures={TOKENIZE.identifier: procedure},
    )
    assert first.records == second.records
    assert first.configurations["tokenize"] == {"suffix": "!"}
