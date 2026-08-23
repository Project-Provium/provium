"""Lazy pipeline definition catalog."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from importlib.resources import files

from .definition import PipelineDefinition, load_pipeline_json, load_pipeline_yaml


class PipelineCatalogError(ValueError):
    """Raised when pipeline catalog registration or resolution fails."""


PipelineLoader = Callable[[], PipelineDefinition]


@dataclass(frozen=True)
class PipelineCatalogRegistration:
    """Inspectable provenance for one lazy catalog registration."""

    identifier: str
    kind: str
    location: str
    source: str


class PipelineCatalog:
    """Register and lazily resolve pipeline definitions by identifier."""

    def __init__(self) -> None:
        self._loaders: dict[str, PipelineLoader] = {}
        self._resolved: dict[str, PipelineDefinition] = {}
        self._registrations: dict[str, PipelineCatalogRegistration] = {}

    @property
    def identifiers(self) -> tuple[str, ...]:
        """Return registered identifiers in stable sorted order."""
        return tuple(sorted(self._loaders))

    @property
    def registrations(self) -> tuple[PipelineCatalogRegistration, ...]:
        """Return registration provenance in identifier order."""
        return tuple(self._registrations[name] for name in self.identifiers)

    def register(self, definition: PipelineDefinition) -> None:
        """Register an in-memory pipeline definition."""
        if not isinstance(definition, PipelineDefinition):
            raise TypeError("pipeline catalog value must be a PipelineDefinition")
        identifier = definition.pipeline.identifier
        self._reserve(identifier, lambda: definition, "memory", "<memory>", "direct")
        self._resolved[identifier] = definition

    def register_target(self, identifier: str, target: str) -> None:
        """Register a lazy ``module:attribute`` definition target."""
        self._reserve(
            identifier,
            lambda: self._load_target(identifier, target),
            "target",
            target,
            "direct",
        )

    def register_resource(self, identifier: str, package: str, resource: str) -> None:
        """Register a packaged JSON or YAML pipeline definition resource."""
        self._reserve(
            identifier,
            lambda: self._load_resource(identifier, package, resource),
            "resource",
            f"{package}:{resource}",
            "direct",
        )

    def get(self, identifier: str) -> PipelineDefinition:
        """Resolve a registered definition, caching the result."""
        resolved = self._resolved.get(identifier)
        if resolved is not None:
            return resolved
        try:
            loader = self._loaders[identifier]
        except KeyError:
            raise PipelineCatalogError(
                f"pipeline identifier is not registered: {identifier!r}"
            ) from None
        definition = loader()
        self._validate_identity(identifier, definition)
        self._resolved[identifier] = definition
        return definition

    def absorb(self, other: PipelineCatalog, *, source: str) -> None:
        """Copy another catalog's registrations without resolving definitions."""
        duplicates = [
            registration.identifier
            for registration in other.registrations
            if registration.identifier in self._loaders
        ]
        if duplicates:
            identifier = duplicates[0]
            previous = self._registrations[identifier].source
            raise PipelineCatalogError(
                f"duplicate pipeline {identifier!r} from {source}; "
                f"already registered from {previous}"
            )
        for registration in other.registrations:
            self._reserve(
                registration.identifier,
                other._loaders[registration.identifier],
                registration.kind,
                registration.location,
                source,
            )

    def _reserve(
        self,
        identifier: str,
        loader: PipelineLoader,
        kind: str,
        location: str,
        source: str,
    ) -> None:
        if identifier in self._loaders:
            raise PipelineCatalogError(
                f"pipeline identifier already registered: {identifier!r}"
            )
        self._loaders[identifier] = loader
        self._registrations[identifier] = PipelineCatalogRegistration(
            identifier=identifier,
            kind=kind,
            location=location,
            source=source,
        )

    @staticmethod
    def _load_target(identifier: str, target: str) -> PipelineDefinition:
        try:
            module_name, attribute = target.split(":", 1)
            value = getattr(import_module(module_name), attribute)
        except Exception as error:
            raise PipelineCatalogError(
                f"failed to load pipeline {identifier!r} from target "
                f"{target!r}: {error}"
            ) from error
        if not isinstance(value, PipelineDefinition):
            raise PipelineCatalogError(
                f"pipeline target {target!r} must provide a PipelineDefinition"
            )
        return value

    @staticmethod
    def _load_resource(
        identifier: str, package: str, resource: str
    ) -> PipelineDefinition:
        source = f"{package}:{resource}"
        suffix = resource.rsplit(".", 1)[-1].lower()
        if suffix not in {"json", "yaml", "yml"}:
            raise PipelineCatalogError(
                f"pipeline {identifier!r} uses unsupported resource {source!r}"
            )
        try:
            text = files(package).joinpath(resource).read_text(encoding="utf-8")
            if suffix == "json":
                return load_pipeline_json(text, source=source)
            return load_pipeline_yaml(text, source=source)
        except Exception as error:
            raise PipelineCatalogError(
                f"failed to load pipeline {identifier!r} from resource "
                f"{source!r}: {error}"
            ) from error

    @staticmethod
    def _validate_identity(identifier: str, definition: PipelineDefinition) -> None:
        actual = definition.pipeline.identifier
        if actual != identifier:
            raise PipelineCatalogError(
                f"pipeline catalog expected {identifier!r}, resolved {actual!r}"
            )
