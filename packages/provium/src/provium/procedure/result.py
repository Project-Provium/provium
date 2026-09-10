"""Immutable metadata returned by completed procedure executions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from provium.artifact.inspection import FinalizedArtifactInspection
from provium.provenance import ArtifactLineage, ArtifactReference, ProcedureRecord


@dataclass(frozen=True, slots=True)
class ProcedureOutputResult:
    """Describe one declared output's produced or absent final state."""

    path: Path
    reference: ArtifactReference | None = None
    inspection: FinalizedArtifactInspection | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            raise TypeError("procedure output result path must be a Path")
        if (self.reference is None) != (self.inspection is None):
            raise ValueError(
                "procedure output reference and inspection must both be "
                "present or absent"
            )
        if self.reference is not None:
            if not isinstance(self.reference, ArtifactReference):
                raise TypeError(
                    "procedure output reference must be an ArtifactReference"
                )
            if not isinstance(self.inspection, FinalizedArtifactInspection):
                raise TypeError(
                    "procedure output inspection must be a FinalizedArtifactInspection"
                )
            if (
                self.reference.identity != self.inspection.artifact_identity
                or self.reference.artifact_identifier
                != self.inspection.artifact_identifier
            ):
                raise ValueError("procedure output reference does not match inspection")

    @property
    def produced(self) -> bool:
        """Return whether the optional output was produced and finalized."""
        return self.reference is not None


@dataclass(frozen=True, slots=True)
class ProcedureExecutionResult:
    """Describe one completed invocation and its named artifact outputs."""

    identity: str
    procedure: ProcedureRecord | None
    inputs: tuple[ArtifactReference, ...]
    outputs: Mapping[str, ArtifactReference] = field(
        default_factory=dict[str, ArtifactReference]
    )
    lineage: ArtifactLineage = field(default_factory=ArtifactLineage)
    output_results: Mapping[str, ProcedureOutputResult] = field(
        default_factory=dict[str, ProcedureOutputResult]
    )

    def __post_init__(self) -> None:
        if not isinstance(self.identity, str):
            raise TypeError("procedure execution result identity must be a string")
        if not self.identity.strip():
            raise ValueError("procedure execution result identity must be nonempty")
        if self.procedure is not None and not isinstance(
            self.procedure, ProcedureRecord
        ):
            raise TypeError(
                "procedure execution result procedure must be a ProcedureRecord or None"
            )
        if not isinstance(self.inputs, tuple):
            raise TypeError("procedure execution result inputs must be a tuple")
        if any(not isinstance(item, ArtifactReference) for item in self.inputs):
            raise TypeError(
                "procedure execution result inputs must be artifact references"
            )
        if not isinstance(self.outputs, Mapping):
            raise TypeError("procedure execution result outputs must be a mapping")
        normalized_outputs = dict(self.outputs)
        for name, reference in normalized_outputs.items():
            if not isinstance(name, str):
                raise TypeError(
                    "procedure execution result output names must be strings"
                )
            if not name.strip():
                raise ValueError(
                    "procedure execution result output names must be nonempty"
                )
            if not isinstance(reference, ArtifactReference):
                raise TypeError(
                    "procedure execution result outputs must be artifact references"
                )
        if not isinstance(self.lineage, ArtifactLineage):
            raise TypeError(
                "procedure execution result lineage must be an ArtifactLineage"
            )
        if not isinstance(self.output_results, Mapping):
            raise TypeError("procedure execution output_results must be a mapping")
        normalized_results = dict(self.output_results)
        for name, output_result in normalized_results.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    "procedure execution output result names must be nonempty"
                )
            if not isinstance(output_result, ProcedureOutputResult):
                raise TypeError(
                    "procedure execution output results must be "
                    "ProcedureOutputResult values"
                )
            if output_result.reference != normalized_outputs.get(name):
                raise ValueError(
                    "procedure output result does not match outputs mapping"
                )
        if normalized_results and set(normalized_outputs) - set(normalized_results):
            raise ValueError("every produced output must have an output result")
        object.__setattr__(self, "outputs", MappingProxyType(normalized_outputs))
        object.__setattr__(self, "output_results", MappingProxyType(normalized_results))


__all__ = ["ProcedureExecutionResult", "ProcedureOutputResult"]
