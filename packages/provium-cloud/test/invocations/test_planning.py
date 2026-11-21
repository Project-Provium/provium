"""Cloud plans keep execution identity independent of graph registration."""

import pytest

from provium_cloud.deployment import CloudDeployment
from provium_cloud.invocations.planning import prepare_pipeline, prepare_procedure


@pytest.fixture
def deployment():
    return CloudDeployment.model_validate(
        {
            "provider": "aws",
            "region": "us-east-1",
            "state": {"backend": "custom"},
            "artifacts": {"backend": "custom"},
            "defaults": {"job_queue": "cpu", "job_definition": "worker:1"},
        }
    )


@pytest.fixture
def graph():
    return {
        "inputs": {"document": "provium_text_pipeline_example.DocumentV1"},
        "nodes": {
            "tokenize": {
                "procedure": "provium_text_pipeline_example.TokenizeV1",
                "inputs": {"source": "$inputs.document"},
                "tags": ["text"],
            },
            "count": {
                "procedure": "provium_text_pipeline_example.CountWordsV1",
                "inputs": {"tokens": "$nodes.tokenize.destination"},
            },
        },
        "targets": {
            "tokens": "$nodes.tokenize.destination",
            "count": "$nodes.count.word_count",
        },
    }


def test_unnamed_plan_and_target_projection(graph, deployment):
    plan = prepare_pipeline(graph, deployment, targets=["tokens"], force=["tags.text"])
    assert list(plan.tasks) == ["tokenize"]
    assert plan.tasks["tokenize"].force
    assert "name" not in plan.graph.model_dump()
    assert plan.targets == {"tokens": "$nodes.tokenize.destination"}
    assert plan.tasks["tokenize"].settings.job_queue == "cpu"


def test_standalone_procedure_is_the_same_computation_plan(deployment, graph):
    pipeline = prepare_pipeline(graph, deployment)
    procedure = prepare_procedure(
        "provium_text_pipeline_example.TokenizeV1", deployment
    )
    assert procedure.tasks["procedure"].record == pipeline.tasks["tokenize"].record
    assert procedure.graph.inputs == {
        "source": "provium_text_pipeline_example.DocumentV1"
    }
    assert procedure.targets == {"destination": "$nodes.procedure.destination"}


def test_unknown_names_targets_and_force_selectors_fail(graph, deployment):
    with pytest.raises(ValueError):
        prepare_pipeline({**graph, "name": "registered"}, deployment)
    with pytest.raises(ValueError, match="target"):
        prepare_pipeline(graph, deployment, targets=["missing"])
    with pytest.raises(ValueError, match="force"):
        prepare_pipeline(graph, deployment, force=["nodes.missing"])


def test_force_all_and_overrides(graph, deployment):
    plan = prepare_pipeline(
        graph, deployment, force=True, overrides={"job_queue": "urgent"}
    )
    assert all(task.force for task in plan.tasks.values())
    assert all(task.settings.job_queue == "urgent" for task in plan.tasks.values())


def test_pipeline_file_loading(tmp_path, graph, deployment):
    import yaml

    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(graph))
    assert prepare_pipeline(path, deployment).graph.inputs == graph["inputs"]


def test_standalone_invalid_configuration_and_unsupported_fields(
    deployment, monkeypatch
):
    from types import SimpleNamespace as Object
    from unittest.mock import Mock

    with pytest.raises(ValueError, match="no configuration"):
        prepare_procedure(
            "provium_text_pipeline_example.TokenizeV1",
            deployment,
            configuration={"unsupported": 1},
        )
    field = Object(
        name="source", value_type=None, artifact_identifier="artifact", repeated=True
    )
    metadata = Object(inputs=[field], setup_inputs=[], outputs=[])
    catalog = Mock()
    catalog.resolve.return_value.resolve_contract.return_value = Object(
        metadata=metadata
    )
    monkeypatch.setattr(
        "provium_cloud.invocations.planning.discover_procedure_catalogs",
        lambda: catalog,
    )
    with pytest.raises(ValueError, match="single-value"):
        prepare_procedure("custom", deployment)
    metadata.inputs = [
        Object(name="setup_source", value_type=None, artifact_identifier="artifact")
    ]
    metadata.setup_inputs = [field]
    with pytest.raises(ValueError, match="conflicting"):
        prepare_procedure("custom", deployment)
