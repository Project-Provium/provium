"""Local task execution support."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from provium.procedure.config import ConfigurationSnapshot, ProcedureConfig
from provium_pipeline.compiler.models import CompiledBindingPlan


class BindingResolutionError(RuntimeError):
    """Resolved artifacts violate a frozen binding plan."""


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
    "BindingResolutionError",
    "ConfigurationSnapshotMismatchError",
    "PreparedProcedureCache",
    "PreparedProcedureKey",
    "resolve_binding_references",
    "verify_configuration_snapshot",
]
