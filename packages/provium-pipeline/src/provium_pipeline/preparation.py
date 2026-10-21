"""Load and validate a selected graph without importing execution machinery."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from .models import (
    ComputeProfile,
    PipelineConfiguration,
    PipelineDefinition,
    Settings,
    load_documents,
)

if TYPE_CHECKING:
    from .persistence.interface import PersistenceBackend
    from .planning import Plan


def load_pipeline(
    *,
    files: Sequence[str] = (),
    name: str | None = None,
    backend: PersistenceBackend | None = None,
    scratch_directory: str | None = None,
) -> PipelineConfiguration:
    if name:
        if backend is None:
            raise ValueError(
                "a persistence backend is required for a registered pipeline"
            )
        if files:
            raise ValueError("select files or a registered pipeline, not both")
        return PipelineConfiguration.model_validate(
            backend.get("PipelineConfiguration", name)
        )
    graphs = [
        doc
        for doc in load_documents(list(files), scratch_directory=scratch_directory)
        if isinstance(doc, PipelineConfiguration)
    ]
    if len(graphs) != 1:
        raise ValueError("select exactly one pipeline with -f or a registered name")
    return graphs[0]


@dataclass
class PreparedPipeline:
    plan: Plan
    definition: PipelineDefinition | None
    profile: ComputeProfile | None
    backend: PersistenceBackend | None
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
    name: str | None = None,
    backend: PersistenceBackend | None = None,
    scratch_directory: str | None = None,
    definition_reference: str | None = None,
    profile_name: str | None = None,
    workers: list[dict[str, Any]] | None = None,
    only: list[str] | None = None,
    force: list[str] | None = None,
) -> PreparedPipeline:
    from .definitions import load_definition
    from .planning import build_plan, resolve_settings

    documents = load_documents(list(files), scratch_directory=scratch_directory)
    graphs = [doc for doc in documents if isinstance(doc, PipelineConfiguration)]
    profiles = {doc.name: doc for doc in documents if isinstance(doc, ComputeProfile)}
    if name:
        if backend is None:
            raise ValueError(
                "a persistence backend is required for a registered pipeline"
            )
        if files:
            raise ValueError("select files or a registered pipeline, not both")
        graph = PipelineConfiguration.model_validate(
            backend.get("PipelineConfiguration", name)
        )
    else:
        if len(graphs) != 1:
            raise ValueError("select exactly one pipeline with -f or a registered name")
        graph = graphs[0]
    definition = load_definition(graph.name, definition_reference)
    profile = None
    if profile_name:
        if backend:
            profile = ComputeProfile.model_validate(
                backend.get("ComputeProfile", profile_name)
            )
        elif profile_name in profiles:
            profile = profiles[profile_name]
        else:
            raise ValueError(f"unknown profile: {profile_name}")
    overrides = workers or []
    if backend:
        registered_definition = backend.definition(graph.name)
        if registered_definition:
            if definition and definition.snapshot() != registered_definition.snapshot():
                raise ValueError("definition differs from registered snapshot")
            definition = registered_definition
    plan = build_plan(graph, definition)
    selected_profile = profile or ComputeProfile(name="default")
    if profile is not None and definition is not None:
        if profile.name not in definition.profiles:
            raise ValueError(f"profile not allowed: {profile.name}")
    settings = resolve_settings(
        plan.configuration,
        selected_profile,
        only=only or [],
        force=force or [],
        overrides=overrides,
    )

    return PreparedPipeline(plan, definition, profile, backend, overrides, settings)
