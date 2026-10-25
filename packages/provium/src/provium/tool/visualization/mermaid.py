"""Mermaid source generation and rendering."""

from __future__ import annotations

import shutil
import subprocess
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory

from provium.provenance import ArtifactLineage
from provium.tool.visualization.errors import (
    BackendUnavailableError,
    UnsupportedFormatError,
)


def _require_lineage(lineage: ArtifactLineage) -> None:
    if not isinstance(lineage, ArtifactLineage):
        raise TypeError("lineage must be an ArtifactLineage")


def _primary(value: str) -> str:
    return (
        "<span style='color:#0F172A;font-weight:700'>"
        f"{escape(value, quote=True)}</span>"
    )


def _field(label: str, value: str, *, color: str) -> str:
    return (
        f"<span style='color:{color};font-weight:700'>{label}</span> "
        "<span style='font-family:monospace;color:#475569'>"
        f"{escape(value, quote=True)}</span>"
    )


def lineage_to_mermaid(
    lineage: ArtifactLineage,
    *,
    show_artifact_identities: bool = False,
    show_procedure_versions: bool = False,
    show_execution_identities: bool = False,
) -> str:
    """Return a deterministic Mermaid flowchart for an artifact lineage."""
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
        "flowchart LR",
        "    classDef artifact fill:#F8FAFC,stroke:#38BDF8,"
        "color:#0F172A,stroke-width:1.5px",
        "    classDef procedure fill:#FAF5FF,stroke:#A78BFA,"
        "color:#0F172A,stroke-width:1.5px",
        "    linkStyle default stroke:#CBD5E1,stroke-width:1.5px",
    ]
    for identity, node in artifact_nodes.items():
        reference = lineage.artifacts[identity].reference
        label_parts = [_primary(reference.artifact_identifier)]
        if show_artifact_identities:
            label_parts.append(_field("Identity:", reference.identity, color="#0284C7"))
        label = "<br/>".join(label_parts)
        lines.append(f'    {node}["{label}"]:::artifact')
    for identity, node in execution_nodes.items():
        procedure = lineage.executions[identity].procedure
        label_parts = [_primary(procedure.name)]
        if show_procedure_versions:
            label_parts.append(_field("Version:", procedure.version, color="#7C3AED"))
        if show_execution_identities:
            label_parts.append(_field("Execution Identity:", identity, color="#64748B"))
        label = "<br/>".join(label_parts)
        lines.append(f'    {node}(["{label}"]):::procedure')
    for identity in sorted(lineage.executions):
        execution = lineage.executions[identity]
        execution_node = execution_nodes[identity]
        for reference in execution.inputs:
            lines.append(
                f"    {artifact_nodes[reference.identity]} --> {execution_node}"
            )
        for reference in execution.outputs:
            lines.append(
                f"    {execution_node} --> {artifact_nodes[reference.identity]}"
            )
    return "\n".join(lines) + "\n"


def render(
    lineage: ArtifactLineage,
    format: str,
    *,
    show_artifact_identities: bool = False,
    show_procedure_versions: bool = False,
    show_execution_identities: bool = False,
) -> bytes:
    """Render a lineage using the optional Mermaid CLI backend."""
    supported = {"pdf", "png", "svg"}
    if format not in supported:
        choices = ", ".join(sorted(supported))
        raise UnsupportedFormatError(
            f"Mermaid does not support format {format!r}; supported formats: {choices}"
        )
    executable = shutil.which("mmdc")
    if executable is None:
        raise BackendUnavailableError(
            "Mermaid rendering requires the 'mmdc' executable on PATH"
        )
    source = lineage_to_mermaid(
        lineage,
        show_artifact_identities=show_artifact_identities,
        show_procedure_versions=show_procedure_versions,
        show_execution_identities=show_execution_identities,
    )
    with TemporaryDirectory(prefix="provium-mermaid-") as directory:
        output = Path(directory) / f"lineage.{format}"
        subprocess.run(
            [executable, "--input", "-", "--output", str(output)],
            input=source,
            check=True,
            text=True,
            capture_output=True,
        )
        return output.read_bytes()
