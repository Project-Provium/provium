from collections.abc import Iterable
from types import SimpleNamespace
from typing import cast

from provium_pipeline.graphs import (
    PipelineGraphSource,
    TaskGraphSource,
    artifact_lineage_graph,
    pipeline_definition_graph,
    record_execution_graph,
    run_task_graph,
)


def test_pipeline_definition_graph_is_deterministic_and_tracks_dependencies() -> None:
    pipeline = SimpleNamespace(
        nodes=(
            SimpleNamespace(
                identifier="detect",
                input_bindings=(
                    SimpleNamespace(references=("load.document", "source")),
                ),
            ),
            SimpleNamespace(identifier="load", input_bindings=()),
        )
    )

    graph = pipeline_definition_graph(cast(PipelineGraphSource, pipeline))

    assert [node.identifier for node in graph.nodes] == ["detect", "load"]
    assert [(edge.source, edge.target) for edge in graph.edges] == [("load", "detect")]


def tasks() -> tuple[object, ...]:
    return (
        SimpleNamespace(
            identifier="task-b",
            record_key="record-2",
            node_identifier="detect",
            state=SimpleNamespace(value="running"),
            dependencies=("task-a",),
        ),
        SimpleNamespace(
            identifier="task-a",
            record_key="record-1",
            node_identifier="load",
            state=SimpleNamespace(value="succeeded"),
            dependencies=(),
        ),
        SimpleNamespace(
            identifier="task-c",
            record_key="record-1",
            node_identifier="detect",
            state=SimpleNamespace(value="ready"),
            dependencies=("task-a",),
        ),
    )


def test_run_and_record_graphs_include_state_and_only_visible_edges() -> None:
    graph_tasks = cast(Iterable[TaskGraphSource], tasks())
    run_graph = run_task_graph(graph_tasks)
    record_graph = record_execution_graph(graph_tasks, "record-1")

    assert [(node.identifier, node.state) for node in run_graph.nodes] == [
        ("task-a", "succeeded"),
        ("task-b", "running"),
        ("task-c", "ready"),
    ]
    assert [(edge.source, edge.target) for edge in run_graph.edges] == [
        ("task-a", "task-b"),
        ("task-a", "task-c"),
    ]
    assert [node.identifier for node in record_graph.nodes] == ["task-a", "task-c"]
    assert [(edge.source, edge.target) for edge in record_graph.edges] == [
        ("task-a", "task-c")
    ]


def test_artifact_lineage_graph_delegates_to_core_renderer() -> None:
    seen: list[object] = []
    artifact = object()

    result = artifact_lineage_graph(
        artifact,
        renderer=lambda value: seen.append(value) or "digraph lineage {}",
    )

    assert result == "digraph lineage {}"
    assert seen == [artifact]
