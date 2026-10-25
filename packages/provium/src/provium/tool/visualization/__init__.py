"""Generate and render visual representations of artifact provenance."""

from __future__ import annotations

from collections.abc import Callable

from provium.provenance import ArtifactLineage
from provium.tool.visualization import graphviz, mermaid
from provium.tool.visualization.errors import (
    BackendUnavailableError,
    UnsupportedFormatError,
)

lineage_to_dot = graphviz.lineage_to_dot
lineage_to_mermaid = mermaid.lineage_to_mermaid
_render_graphviz = graphviz.render
_render_mermaid = mermaid.render

_Renderer = Callable[..., bytes]


def _require_lineage(lineage: ArtifactLineage) -> None:
    if not isinstance(lineage, ArtifactLineage):
        raise TypeError("lineage must be an ArtifactLineage")


def render_lineage(
    lineage: ArtifactLineage,
    *,
    format: str,
    backend: str = "auto",
    show_artifact_identities: bool = False,
    show_procedure_versions: bool = False,
    show_execution_identities: bool = False,
) -> bytes:
    """Render an artifact lineage with the selected optional backend."""
    _require_lineage(lineage)
    renderers: dict[str, _Renderer] = {
        "graphviz": _render_graphviz,
        "mermaid": _render_mermaid,
    }
    label_options = {
        name: enabled
        for name, enabled in {
            "show_artifact_identities": show_artifact_identities,
            "show_procedure_versions": show_procedure_versions,
            "show_execution_identities": show_execution_identities,
        }.items()
        if enabled
    }
    if backend != "auto":
        try:
            renderer = renderers[backend]
        except KeyError as error:
            raise ValueError(f"unknown visualization backend: {backend!r}") from error
        return renderer(lineage, format, **label_options)

    failures: list[Exception] = []
    for renderer in renderers.values():
        try:
            return renderer(lineage, format, **label_options)
        except (BackendUnavailableError, UnsupportedFormatError) as error:
            failures.append(error)
    details = "; ".join(str(error) for error in failures)
    raise RuntimeError(f"no visualization backend can render {format!r}: {details}")


__all__ = [
    "BackendUnavailableError",
    "UnsupportedFormatError",
    "lineage_to_dot",
    "lineage_to_mermaid",
    "render_lineage",
]
