"""Validate a local pipeline without durable infrastructure."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from .models import (
    ExecutionSettings,
    PipelineConfiguration,
    PipelineDefinition,
    Settings,
    load_documents,
)

if TYPE_CHECKING:
    from .planning import Plan


def load_pipeline(
    *,
    files: Sequence[str] = (),
    scratch_directory: str | None = None,
) -> PipelineConfiguration:
    graphs = load_documents(files, scratch_directory=scratch_directory)
    if len(graphs) != 1:
        raise ValueError("select exactly one pipeline with -f")
    return graphs[0]


@dataclass
class PreparedPipeline:
    plan: Plan
    definition: PipelineDefinition | None
    overrides: list[dict[str, Any]]
    settings: dict[str, Settings]

    def summary(self) -> dict[str, Any]:
        return {
            "pipeline": self.plan.configuration.name,
            "order": self.plan.order,
            "settings": {name: asdict(value) for name, value in self.settings.items()},
        }


def prepare_pipeline(
    *,
    files: Sequence[str] = (),
    scratch_directory: str | None = None,
    definition_reference: str | None = None,
    workers: list[dict[str, Any]] | None = None,
    only: list[str] | None = None,
    force: list[str] | None = None,
) -> PreparedPipeline:
    from .definitions import load_definition
    from .planning import build_plan, resolve_settings

    graph = load_pipeline(files=files, scratch_directory=scratch_directory)
    definition = load_definition(graph.name, definition_reference)
    plan = build_plan(graph, definition)
    overrides = workers or []
    settings = resolve_settings(
        plan.configuration,
        ExecutionSettings(),
        only=only or [],
        force=force or [],
        overrides=overrides,
    )
    return PreparedPipeline(plan, definition, overrides, settings)
