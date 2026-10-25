"""Graphviz source generation and rendering."""

from __future__ import annotations

from html import escape
from importlib import import_module
from typing import Protocol, cast

from provium.provenance import ArtifactLineage
from provium.tool.visualization.errors import (
    BackendUnavailableError,
    UnsupportedFormatError,
)


class _GraphvizSource(Protocol):
    def pipe(self) -> bytes: ...


class _GraphvizSourceFactory(Protocol):
    def __call__(self, source: str, *, format: str) -> _GraphvizSource: ...


class _GraphvizModule(Protocol):
    FORMATS: set[str]
    Source: _GraphvizSourceFactory


class _GraphvizBackendModule(Protocol):
    ExecutableNotFound: type[Exception]


def _require_lineage(lineage: ArtifactLineage) -> None:
    if not isinstance(lineage, ArtifactLineage):
        raise TypeError("lineage must be an ArtifactLineage")


def _primary(value: str) -> str:
    return f'<FONT COLOR="#0F172A"><B>{escape(value, quote=True)}</B></FONT>'


def _field(label: str, value: str, *, color: str) -> str:
    return (
        f'<FONT COLOR="{color}"><B>{label}</B></FONT> '
        f'<FONT FACE="monospace" COLOR="#475569">'
        f"{escape(value, quote=True)}</FONT>"
    )


def lineage_to_dot(
    lineage: ArtifactLineage,
    *,
    show_artifact_identities: bool = False,
    show_procedure_versions: bool = False,
    show_execution_identities: bool = False,
) -> str:
    """Return deterministic DOT source without requiring Graphviz."""
    _require_lineage(lineage)
    artifact_nodes = {
        identity: f"artifact_{index}"
        for index, identity in enumerate(sorted(lineage.artifacts))
    }
    execution_nodes = {
        identity: f"execution_{index}"
        for index, identity in enumerate(sorted(lineage.executions))
    }
    lines = [
        "digraph provium_lineage {",
        "    rankdir=LR;",
        '    node [shape="box", fontname="Helvetica"];',
        '    edge [color="#CBD5E1", penwidth="1.5", arrowsize="0.7"];',
    ]
    for identity, node in artifact_nodes.items():
        reference = lineage.artifacts[identity].reference
        label_parts = [_primary(reference.artifact_identifier)]
        if show_artifact_identities:
            label_parts.append(_field("Identity:", reference.identity, color="#0284C7"))
        label = "<BR/>".join(label_parts)
        lines.append(
            f'    {node} [label=<{label}>, color="#38BDF8", '
            'fillcolor="#F8FAFC", fontcolor="#0F172A", '
            'penwidth="1.5", style="filled"];'
        )
    for identity, node in execution_nodes.items():
        execution = lineage.executions[identity]
        procedure = execution.procedure
        label_parts = [_primary(procedure.name)]
        if show_procedure_versions:
            label_parts.append(_field("Version:", procedure.version, color="#7C3AED"))
        if show_execution_identities:
            label_parts.append(_field("Execution Identity:", identity, color="#64748B"))
        label = "<BR/>".join(label_parts)
        lines.append(
            f'    {node} [label=<{label}>, color="#A78BFA", '
            'fillcolor="#FAF5FF", fontcolor="#0F172A", '
            'penwidth="1.5", style="rounded,filled"];'
        )
        for reference in execution.inputs:
            lines.append(f"    {artifact_nodes[reference.identity]} -> {node};")
        for reference in execution.outputs:
            lines.append(f"    {node} -> {artifact_nodes[reference.identity]};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _graph(
    lineage: ArtifactLineage,
    *,
    format: str,
    show_artifact_identities: bool = False,
    show_procedure_versions: bool = False,
    show_execution_identities: bool = False,
) -> _GraphvizSource:
    try:
        graphviz = cast(_GraphvizModule, import_module("graphviz"))
    except ImportError as error:
        raise BackendUnavailableError(
            "Graphviz rendering requires the 'graphviz' Python package and "
            "Graphviz executable"
        ) from error
    if format not in graphviz.FORMATS:
        supported = ", ".join(sorted(graphviz.FORMATS))
        raise UnsupportedFormatError(
            f"Graphviz does not support format {format!r}; supported formats: "
            f"{supported}"
        )
    source = lineage_to_dot(
        lineage,
        show_artifact_identities=show_artifact_identities,
        show_procedure_versions=show_procedure_versions,
        show_execution_identities=show_execution_identities,
    )
    return graphviz.Source(source, format=format)


def render(
    lineage: ArtifactLineage,
    format: str,
    *,
    show_artifact_identities: bool = False,
    show_procedure_versions: bool = False,
    show_execution_identities: bool = False,
) -> bytes:
    """Render a lineage using the optional Graphviz backend."""
    graph = _graph(
        lineage,
        format=format,
        show_artifact_identities=show_artifact_identities,
        show_procedure_versions=show_procedure_versions,
        show_execution_identities=show_execution_identities,
    )
    backend = cast(_GraphvizBackendModule, import_module("graphviz.backend"))
    try:
        return graph.pipe()
    except backend.ExecutableNotFound as error:
        raise BackendUnavailableError(
            "Graphviz rendering requires a Graphviz executable on PATH"
        ) from error
