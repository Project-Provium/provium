"""Deterministic queries over artifacts bound to pipeline runs."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunArtifactBinding:
    """A run-specific use of an artifact, including its production provenance."""

    artifact_identity: str
    run_id: str
    record_key: str
    node_id: str
    output_field: str
    disposition: str
    origin_run_id: str
    origin_task_id: str


@dataclass(frozen=True, slots=True)
class InputSetArtifactBinding:
    """An artifact bound to a named field of one input-set record."""

    input_set_id: str
    record_key: str
    input_field: str
    artifact_identity: str


@dataclass(frozen=True, slots=True)
class ArtifactQuery:
    """Composable filters for artifacts associated with one run."""

    run_id: str
    record_key: str | None = None
    node_id: str | None = None
    output_field: str | None = None
    include_locations: bool = False


@dataclass(frozen=True, slots=True)
class ArtifactQueryResult[BindingT]:
    """A matching binding and, when requested, its active locations."""

    binding: BindingT
    locations: tuple[object, ...] = ()


class ArtifactQueryService:
    """Query an immutable view of run artifact bindings."""

    def __init__(
        self,
        *,
        bindings: Iterable[RunArtifactBinding],
        location_lookup: Callable[[str], Iterable[object]],
        input_set_bindings: Iterable[InputSetArtifactBinding] = (),
    ) -> None:
        self._bindings = tuple(bindings)
        self._input_set_bindings = tuple(input_set_bindings)
        self._location_lookup = location_lookup

    def query(
        self, query: ArtifactQuery
    ) -> tuple[ArtifactQueryResult[RunArtifactBinding], ...]:
        matches = (
            binding
            for binding in self._bindings
            if binding.run_id == query.run_id
            and _matches(query.record_key, binding.record_key)
            and _matches(query.node_id, binding.node_id)
            and _matches(query.output_field, binding.output_field)
        )
        ordered = sorted(
            matches,
            key=lambda binding: (
                binding.record_key,
                binding.node_id,
                binding.output_field,
                binding.artifact_identity,
                binding.origin_run_id,
                binding.origin_task_id,
            ),
        )
        return tuple(
            ArtifactQueryResult(
                binding=binding,
                locations=(
                    self.locations(binding.artifact_identity)
                    if query.include_locations
                    else ()
                ),
            )
            for binding in ordered
        )

    def query_input_set(
        self,
        input_set_id: str,
        *,
        include_locations: bool = False,
    ) -> tuple[ArtifactQueryResult[InputSetArtifactBinding], ...]:
        bindings = sorted(
            (
                binding
                for binding in self._input_set_bindings
                if binding.input_set_id == input_set_id
            ),
            key=lambda binding: (
                binding.record_key,
                binding.input_field,
                binding.artifact_identity,
            ),
        )
        return tuple(
            ArtifactQueryResult(
                binding=binding,
                locations=(
                    self.locations(binding.artifact_identity)
                    if include_locations
                    else ()
                ),
            )
            for binding in bindings
        )

    def locations(self, artifact_identity: str) -> tuple[object, ...]:
        return tuple(sorted(self._location_lookup(artifact_identity), key=str))


def _matches(expected: str | None, actual: str) -> bool:
    return expected is None or expected == actual


__all__ = [
    "ArtifactQuery",
    "ArtifactQueryResult",
    "ArtifactQueryService",
    "InputSetArtifactBinding",
    "RunArtifactBinding",
]
