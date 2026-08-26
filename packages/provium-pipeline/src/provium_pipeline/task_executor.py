"""Local task execution support."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import ValidationError

from provium.artifact.binding import ArtifactReadBinding, ArtifactWriteBinding
from provium.artifact.definition import Artifact
from provium.procedure.config import ConfigurationSnapshot, ProcedureConfig
from provium.procedure.definition import ProcedureContractMetadata
from provium_pipeline.artifact import (
    ArtifactLocation,
    ArtifactStore,
    ManagedArtifactDescriptor,
    MaterializationCleanup,
    MaterializedArtifact,
)
from provium_pipeline.compiler.catalogs import ArtifactCatalogCollection
from provium_pipeline.compiler.models import CompiledBindingPlan


def _remove_materialization(path: Path) -> None:
    path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """One managed artifact and its active storage locations."""

    descriptor: ManagedArtifactDescriptor
    locations: tuple[ArtifactLocation, ...]


class AttemptMaterializations:
    """Own caller-cleaned materialized inputs for one task attempt."""

    def __init__(
        self,
        *,
        remove: Callable[[Path], None] = _remove_materialization,
    ) -> None:
        self._remove = remove
        self._owned: list[Path] = []
        self._closed = False

    def track(self, materialized: MaterializedArtifact) -> Path:
        """Record cleanup ownership and return the verified local path."""
        if self._closed:
            raise RuntimeError("attempt materializations are closed")
        if materialized.cleanup is MaterializationCleanup.REQUIRED:
            self._owned.append(materialized.path)
        return materialized.path

    def close(self) -> None:
        """Remove all caller-owned paths exactly once."""
        if self._closed:
            return
        self._closed = True
        owned = tuple(reversed(self._owned))
        self._owned.clear()
        failure: BaseException | None = None
        for path in owned:
            try:
                self._remove(path)
            except BaseException as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure


class BindingResolutionError(RuntimeError):
    """Resolved artifacts violate a frozen binding plan."""


def _build_write_binding(
    artifact: type[Artifact[Any, Any]],
    path: Path,
) -> ArtifactWriteBinding[Any]:
    return ArtifactWriteBinding(artifact, path)


def build_output_bindings(
    expected_fields: Sequence[str],
    contract: ProcedureContractMetadata,
    catalogs: ArtifactCatalogCollection,
    workspace: Path,
    *,
    binding_factory: Callable[[type[Artifact[Any, Any]], Path], object] = (
        _build_write_binding
    ),
) -> dict[str, object]:
    """Build output bindings after verifying the frozen field contract."""
    installed_fields = tuple(field.name for field in contract.outputs)
    if tuple(expected_fields) != installed_fields:
        raise BindingResolutionError(
            "frozen output fields do not match the installed procedure contract"
        )
    workspace.mkdir(parents=True, exist_ok=True)
    bindings: dict[str, object] = {}
    for field in contract.outputs:
        artifact = cast(
            type[Artifact[Any, Any]],
            catalogs.resolve(field.artifact_identifier).resolve(),
        )
        bindings[field.name] = binding_factory(
            artifact,
            workspace / f"{field.name}.pa",
        )
    return bindings


def _build_read_binding(
    artifact: type[Artifact[Any, Any]],
    path: Path,
) -> ArtifactReadBinding[Any]:
    return ArtifactReadBinding(artifact, path)


def build_read_binding_value(
    plan: CompiledBindingPlan,
    paths: Sequence[Path],
    catalogs: ArtifactCatalogCollection,
    *,
    binding_factory: Callable[[type[Artifact[Any, Any]], Path], object] = (
        _build_read_binding
    ),
) -> object:
    """Build the core scalar, optional, or repeated read-binding value."""
    artifact = cast(
        type[Artifact[Any, Any]],
        catalogs.resolve(plan.artifact_identifier).resolve(),
    )
    bindings = tuple(binding_factory(artifact, path) for path in paths)
    if plan.maximum == 1:
        return None if not bindings else bindings[0]
    return bindings


def materialize_binding_inputs(
    plan: CompiledBindingPlan,
    identities: Sequence[str],
    artifacts: Mapping[str, StoredArtifact],
    store: ArtifactStore,
    workspace: Path,
    ownership: AttemptMaterializations,
) -> tuple[Path, ...]:
    """Materialize ordered binding identities into an attempt workspace."""
    workspace.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, identity in enumerate(identities):
        try:
            stored = artifacts[identity]
        except KeyError as error:
            raise BindingResolutionError(
                f"artifact identity is not available: {identity!r}"
            ) from error
        destination = workspace / f"{plan.field}-{index:04d}.pa"
        materialized = store.materialize(
            descriptor=stored.descriptor,
            locations=stored.locations,
            destination=destination,
        )
        paths.append(ownership.track(materialized))
    return tuple(paths)


def resolve_binding_references(
    plan: CompiledBindingPlan,
    resolver: Callable[[str], str | None],
) -> tuple[str, ...]:
    """Resolve exact artifact identities in frozen reference order."""
    identities = tuple(
        identity
        for reference in plan.references
        if (identity := resolver(reference)) is not None
    )
    if len(identities) < plan.minimum:
        raise BindingResolutionError(
            f"binding {plan.field!r} requires at least {plan.minimum} artifacts"
        )
    if plan.maximum is not None and len(identities) > plan.maximum:
        raise BindingResolutionError(
            f"binding {plan.field!r} accepts at most {plan.maximum} artifacts"
        )
    return identities


class ConfigurationSnapshotMismatchError(RuntimeError):
    """A frozen configuration no longer matches the installed contract."""


def verify_configuration_snapshot(
    snapshot: ConfigurationSnapshot | None,
    configuration_type: type[ProcedureConfig] | None,
) -> ProcedureConfig | None:
    """Revalidate a frozen configuration and verify its canonical fingerprints."""
    if snapshot is None:
        if configuration_type is None:
            return None
        raise ConfigurationSnapshotMismatchError("configuration snapshot is missing")
    if configuration_type is None:
        raise ConfigurationSnapshotMismatchError(
            "installed procedure does not accept configuration"
        )
    expected_target = (
        f"{configuration_type.__module__}:{configuration_type.__qualname__}"
    )
    if snapshot.model_target != expected_target:
        raise ConfigurationSnapshotMismatchError("configuration model target mismatch")
    try:
        configuration = configuration_type.model_validate(snapshot.value)
    except ValidationError as error:
        raise ConfigurationSnapshotMismatchError(
            "configuration snapshot value is invalid"
        ) from error
    verified = ConfigurationSnapshot.from_configuration(configuration)
    if snapshot.schema_digest != verified.schema_digest:
        raise ConfigurationSnapshotMismatchError("configuration schema digest mismatch")
    if snapshot.value_digest != verified.value_digest:
        raise ConfigurationSnapshotMismatchError("configuration value digest mismatch")
    return configuration


class PreparedProcedure(Protocol):
    """Reusable prepared procedure lifecycle required by the cache."""

    def close(self) -> None:
        """Release the prepared procedure's resources."""


@dataclass(frozen=True, slots=True)
class PreparedProcedureKey:
    """Fingerprints that determine whether prepared state can be reused."""

    procedure: str
    configuration: str
    setup: str


class PreparedProcedureCache[PreparedT: PreparedProcedure]:
    """Own and reuse prepared procedures with identical setup fingerprints."""

    def __init__(self) -> None:
        self._prepared: dict[PreparedProcedureKey, PreparedT] = {}
        self._closed = False

    def get_or_prepare(
        self,
        key: PreparedProcedureKey,
        prepare: Callable[[], PreparedT],
    ) -> PreparedT:
        """Return cached prepared state or create it exactly once."""
        if self._closed:
            raise RuntimeError("prepared procedure cache is closed")
        cached = self._prepared.get(key)
        if cached is not None:
            return cached
        prepared = prepare()
        self._prepared[key] = prepared
        return prepared

    def close(self) -> None:
        """Close every owned prepared procedure exactly once."""
        if self._closed:
            return
        self._closed = True
        prepared_values = tuple(reversed(self._prepared.values()))
        self._prepared.clear()
        failure: BaseException | None = None
        for prepared in prepared_values:
            try:
                prepared.close()
            except BaseException as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure


__all__ = [
    "AttemptMaterializations",
    "BindingResolutionError",
    "ConfigurationSnapshotMismatchError",
    "PreparedProcedureCache",
    "PreparedProcedureKey",
    "StoredArtifact",
    "build_output_bindings",
    "build_read_binding_value",
    "materialize_binding_inputs",
    "resolve_binding_references",
    "verify_configuration_snapshot",
]
