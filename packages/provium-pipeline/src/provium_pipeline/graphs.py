"""Deterministic graph read models for pipeline definitions and runs."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol


class BindingGraphSource(Protocol):
    references: tuple[str, ...]


class PipelineNodeGraphSource(Protocol):
    identifier: str
    input_bindings: tuple[BindingGraphSource, ...]


class PipelineGraphSource(Protocol):
    nodes: tuple[PipelineNodeGraphSource, ...]


class _State(Protocol):
    value: str


class TaskGraphSource(Protocol):
    identifier: object
    record_key: object
    node_identifier: str
    state: _State
    dependencies: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class GraphNode:
    identifier: str
    label: str
    state: str | None = None


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class GraphDocument:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


def pipeline_definition_graph(pipeline: PipelineGraphSource) -> GraphDocument:
    identifiers = {node.identifier for node in pipeline.nodes}
    nodes = tuple(
        GraphNode(identifier=node.identifier, label=node.identifier)
        for node in sorted(pipeline.nodes, key=lambda item: item.identifier)
    )
    edges = {
        GraphEdge(source=reference.split(".", 1)[0], target=node.identifier)
        for node in pipeline.nodes
        for binding in node.input_bindings
        for reference in binding.references
        if reference.split(".", 1)[0] in identifiers
    }
    return GraphDocument(nodes=nodes, edges=_ordered_edges(edges))


def run_task_graph(tasks: Iterable[TaskGraphSource]) -> GraphDocument:
    return _task_graph(tuple(tasks))


def record_execution_graph(
    tasks: Iterable[TaskGraphSource],
    record_key: object,
) -> GraphDocument:
    return _task_graph(
        tuple(task for task in tasks if str(task.record_key) == str(record_key))
    )


def artifact_lineage_graph(
    artifact: object,
    *,
    renderer: Callable[[object], str],
) -> str:
    """Delegate artifact graph rendering to the core lineage implementation."""
    return renderer(artifact)


def _task_graph(tasks: tuple[TaskGraphSource, ...]) -> GraphDocument:
    ordered_tasks = sorted(tasks, key=lambda task: str(task.identifier))
    identifiers = {str(task.identifier) for task in ordered_tasks}
    nodes = tuple(
        GraphNode(
            identifier=str(task.identifier),
            label=task.node_identifier,
            state=task.state.value,
        )
        for task in ordered_tasks
    )
    edges = {
        GraphEdge(source=str(dependency), target=str(task.identifier))
        for task in ordered_tasks
        for dependency in task.dependencies
        if str(dependency) in identifiers
    }
    return GraphDocument(nodes=nodes, edges=_ordered_edges(edges))


def _ordered_edges(edges: Iterable[GraphEdge]) -> tuple[GraphEdge, ...]:
    return tuple(sorted(edges, key=lambda edge: (edge.source, edge.target)))


__all__ = [
    "GraphDocument",
    "GraphEdge",
    "GraphNode",
    "PipelineGraphSource",
    "TaskGraphSource",
    "artifact_lineage_graph",
    "pipeline_definition_graph",
    "record_execution_graph",
    "run_task_graph",
]
