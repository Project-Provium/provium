"""Installed pipeline catalog discovery with provenance diagnostics."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Protocol, cast

from .catalog import (
    PipelineCatalog,
    PipelineCatalogError,
    PipelineCatalogRegistration,
)

PIPELINE_CATALOG_ENTRY_POINT_GROUP = "provium.pipeline_catalogs"


class _Distribution(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...


class _EntryPoint(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def value(self) -> str: ...

    @property
    def dist(self) -> _Distribution | None: ...

    def load(self) -> Any: ...


@dataclass(frozen=True)
class PipelineDiscoveryDiagnostic:
    """Provenance or failure information for one discovered definition."""

    entry_point: str
    distribution: str
    distribution_version: str
    definition_identifier: str | None
    registration_kind: str
    location: str
    error_chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineDiscoveryResult:
    """A merged lazy catalog and complete discovery diagnostics."""

    catalog: PipelineCatalog
    diagnostics: tuple[PipelineDiscoveryDiagnostic, ...]


def discover_pipeline_catalogs(
    *, entry_points: Iterable[_EntryPoint] | None = None
) -> PipelineDiscoveryResult:
    """Discover and merge installed pipeline catalogs without resolving definitions."""
    if entry_points is None:
        discovered = metadata.entry_points().select(
            group=PIPELINE_CATALOG_ENTRY_POINT_GROUP
        )
        candidates = cast(Iterable[_EntryPoint], discovered)
    else:
        candidates = entry_points
    merged = PipelineCatalog()
    diagnostics: list[PipelineDiscoveryDiagnostic] = []
    for entry_point in sorted(
        candidates,
        key=lambda item: (_distribution_name(item), item.name, item.value),
    ):
        distribution = _distribution_name(entry_point)
        version = _distribution_version(entry_point)
        source = f"entry point {entry_point.name!r} from {distribution} {version}"
        provider: object = None
        try:
            provider = _require_catalog(entry_point.load())
            merged.absorb(provider, source=source)
        except Exception as error:
            registration = _duplicate_registration(provider, merged, error)
            diagnostics.append(
                PipelineDiscoveryDiagnostic(
                    entry_point=entry_point.name,
                    distribution=distribution,
                    distribution_version=version,
                    definition_identifier=(
                        registration.identifier if registration is not None else None
                    ),
                    registration_kind=(
                        registration.kind if registration is not None else "catalog"
                    ),
                    location=(
                        registration.location
                        if registration is not None
                        else entry_point.value
                    ),
                    error_chain=_error_chain(error),
                )
            )
            continue
        diagnostics.extend(
            PipelineDiscoveryDiagnostic(
                entry_point=entry_point.name,
                distribution=distribution,
                distribution_version=version,
                definition_identifier=registration.identifier,
                registration_kind=registration.kind,
                location=registration.location,
            )
            for registration in provider.registrations
        )
    return PipelineDiscoveryResult(merged, tuple(diagnostics))


def _require_catalog(value: object) -> PipelineCatalog:
    if not isinstance(value, PipelineCatalog):
        raise TypeError("entry point must provide a PipelineCatalog")
    return value


def _duplicate_registration(
    provider: object, merged: PipelineCatalog, error: Exception
) -> PipelineCatalogRegistration | None:
    if not isinstance(error, PipelineCatalogError) or not isinstance(
        provider, PipelineCatalog
    ):
        return None
    return next(
        (
            registration
            for registration in provider.registrations
            if registration.identifier in merged.identifiers
        ),
        None,
    )


def _distribution_name(entry_point: _EntryPoint) -> str:
    return entry_point.dist.name if entry_point.dist is not None else "<unknown>"


def _distribution_version(entry_point: _EntryPoint) -> str:
    return entry_point.dist.version if entry_point.dist is not None else "<unknown>"


def _error_chain(error: BaseException) -> tuple[str, ...]:
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return tuple(chain)
