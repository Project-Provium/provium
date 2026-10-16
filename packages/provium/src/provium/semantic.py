"""Versioned semantic provenance, independent of execution UUIDs."""

from collections.abc import Mapping
from typing import Any

from .canonical import canonical_digest
from .provenance import ArtifactLineage, ArtifactReference, ProcedureRecord


def canonical_hash(value: Any) -> str:
    """Hash using core's canonical JSON encoding."""
    return canonical_digest(value)


def computation_key(
    procedure: ProcedureRecord,
    inputs: Mapping[str, Any],
    setup_inputs: Mapping[str, Any],
) -> str:
    """Predict a computation from resolved semantics and input fingerprints."""
    return canonical_hash(
        {
            "version": 1,
            "procedure": procedure.to_dict(),
            "inputs": dict(inputs),
            "setup_inputs": dict(setup_inputs),
        }
    )


def output_key(computation: str, name: str, artifact_identifier: str) -> str:
    """Predict a named output's producing provenance, excluding its body."""
    return canonical_hash(
        {
            "version": 1,
            "computation": computation,
            "output": name,
            "type": artifact_identifier,
        }
    )


def artifact_fingerprint(
    lineage: ArtifactLineage, reference: ArtifactReference
) -> dict[str, str]:
    """Reconstruct producing provenance and retain the body's separate digest."""
    cache: dict[str, dict[str, str]] = {}

    def visit(ref: ArtifactReference) -> dict[str, str]:
        if ref.identity in cache:
            return cache[ref.identity]
        record = lineage.artifact(ref)
        execution = lineage.producing_execution(ref)
        if execution.bindings is None:
            raise ValueError("legacy provenance lacks named bindings")
        inputs: dict[str, Any] = {}
        setup: dict[str, Any] = {}
        output_names: dict[str, str] = {}

        def resolve(value: str | tuple[str, ...] | None) -> Any:
            if value is None:
                return None
            if isinstance(value, tuple):
                return [resolve(item) for item in value]
            return visit(lineage.artifacts[value].reference)

        for key, value in execution.bindings.items():
            scope, _, name = key.partition(".")
            if scope == "outputs":
                output_names[str(value)] = name
            else:
                (inputs if scope == "inputs" else setup)[name] = resolve(value)
        result = {
            "type": ref.artifact_identifier,
            "provenance": output_key(
                computation_key(execution.procedure, inputs, setup),
                output_names[ref.identity],
                ref.artifact_identifier,
            ),
            "content": record.body_digest,
        }
        cache[ref.identity] = result
        return result

    return visit(reference)
