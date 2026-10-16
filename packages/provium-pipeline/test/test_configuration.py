import pytest

from provium_pipeline.models import (
    ComputeProfile,
    PipelineConfiguration,
    load_documents,
)


def test_multidoc_and_duplicate_rejection(tmp_path):
    path = tmp_path / "bundle.yaml"
    path.write_text(
        "kind: PipelineConfiguration\nname: p\nnodes: {}\noutputs: {}\n"
        "---\nkind: ComputeProfile\nname: local\n"
    )
    docs = load_documents([path])
    assert len(docs) == 2
    path.write_text("kind: ComputeProfile\nname: local\nname: other\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_documents([path])


def test_workers_and_limits_resolve_independently():
    profile = ComputeProfile(
        name="cluster",
        workers=[
            {"default": 1},
            {"tags.model": 2},
            {"pipelines.p.nodes.encode": 4},
            {"tags.large": 3},
        ],
        soft_time_limits=[{"tags.model": 20}],
        hard_time_limits=[{"default": 30}],
    )
    assert profile.settings("p", "encode", ["model", "large"]).workers == 4
    assert profile.settings("p", "other", ["model", "large"]).workers == 3
    assert profile.settings("p", "encode", ["model"]).soft == 20
    assert profile.settings("p", "encode", ["model"]).hard == 30
    assert ComputeProfile(name="local").settings("p", "n", []).workers == 1


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_workers(value):
    with pytest.raises(ValueError):
        ComputeProfile(name="x", workers=[{"default": value}])


def test_graph_cycles_and_bad_references():
    graph = PipelineConfiguration(
        name="p",
        nodes={
            "a": {"procedure": "x", "inputs": {"i": "$nodes.b.o"}},
            "b": {"procedure": "x", "inputs": {"i": "$nodes.a.o"}},
        },
        outputs={"o": "$nodes.a.o"},
    )
    with pytest.raises(ValueError, match="cycle"):
        graph.order()
    with pytest.raises(ValueError):
        PipelineConfiguration(name="p", nodes={}, outputs={"o": "$bad.x"})


def test_stable_leftmost_topological_order():
    graph = PipelineConfiguration(
        name="p",
        nodes={
            "a": {"procedure": "x", "inputs": {"i": "$inputs.i"}},
            "b": {"procedure": "x", "inputs": {"i": "$nodes.a.o"}},
            "c": {"procedure": "x", "inputs": {"i": "$inputs.i"}},
        },
        outputs={"b": "$nodes.b.o", "c": "$nodes.c.o"},
    )
    assert graph.order() == ["a", "b", "c"]


def test_unknown_compute_fields_rejected():
    with pytest.raises(ValueError):
        PipelineConfiguration(
            name="p", nodes={"a": {"procedure": "x", "compute": {}}}, outputs={}
        )
