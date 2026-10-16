"""Immutable provenance value records and canonical serialization."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Self, cast


def _require_text(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_object(value: str) -> Mapping[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, Mapping):
        raise TypeError("serialized provenance record must be a JSON object")
    return cast(Mapping[str, Any], decoded)


class _SerializableRecord:
    _shape_name: ClassVar[str]

    def to_json(self) -> str:
        """Serialize this record to canonical JSON."""
        return _canonical_json(cast(Any, self).to_dict())

    @classmethod
    def from_json(cls, value: str) -> Self:
        """Deserialize this record from a JSON object."""
        return cast(Self, cast(Any, cls).from_dict(_json_object(value)))

    @classmethod
    def _expect_keys(cls, value: Mapping[str, Any], keys: set[str]) -> None:
        if not isinstance(value, Mapping) or set(value) != keys:
            raise ValueError(f"invalid {cls._shape_name}")


@dataclass(frozen=True, slots=True)
class ArtifactReference(_SerializableRecord):
    """The persistent identity and catalog type of an artifact."""

    identity: str
    artifact_identifier: str
    _shape_name: ClassVar[str] = "artifact reference"

    def __post_init__(self) -> None:
        _require_text(self.identity, "identity")
        _require_text(self.artifact_identifier, "artifact_identifier")

    def to_dict(self) -> dict[str, str]:
        return {
            "artifact_identifier": self.artifact_identifier,
            "identity": self.identity,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        cls._expect_keys(value, {"artifact_identifier", "identity"})
        return cls(value["identity"], value["artifact_identifier"])


@dataclass(frozen=True, slots=True)
class ArtifactRecord(_SerializableRecord):
    """A finalized artifact digest and its producing execution."""

    reference: ArtifactReference
    body_digest: str
    producer_execution_identity: str
    _shape_name: ClassVar[str] = "artifact record"

    def __post_init__(self) -> None:
        if not isinstance(self.reference, ArtifactReference):
            raise TypeError("reference must be an artifact reference")
        _require_text(self.body_digest, "body_digest")
        _require_text(
            self.producer_execution_identity,
            "producer_execution_identity",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "body_digest": self.body_digest,
            "producer_execution_identity": self.producer_execution_identity,
            "reference": self.reference.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        cls._expect_keys(
            value,
            {"body_digest", "producer_execution_identity", "reference"},
        )
        return cls(
            ArtifactReference.from_dict(value["reference"]),
            value["body_digest"],
            value["producer_execution_identity"],
        )


@dataclass(frozen=True, slots=True)
class ProcedureRecord(_SerializableRecord):
    """A procedure identity and its optional configuration snapshot."""

    name: str
    version: str
    config: Any = None
    config_codec: str | None = None
    _shape_name: ClassVar[str] = "procedure record"

    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        _require_text(self.version, "version")
        if self.config_codec is not None:
            _require_text(self.config_codec, "config_codec")

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config,
            "config_codec": self.config_codec,
            "name": self.name,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        cls._expect_keys(value, {"config", "config_codec", "name", "version"})
        return cls(
            value["name"],
            value["version"],
            value["config"],
            value["config_codec"],
        )


@dataclass(frozen=True, slots=True, init=False)
class ProcedureExecutionRecord(_SerializableRecord):
    """One procedure execution and its artifact edges."""

    identity: str
    procedure: ProcedureRecord
    inputs: tuple[ArtifactReference, ...] = ()
    outputs: tuple[ArtifactReference, ...] = ()
    bindings: Mapping[str, str | tuple[str, ...] | None] | None = None
    _shape_name: ClassVar[str] = "procedure execution record"

    def __init__(
        self,
        identity: str,
        procedure: ProcedureRecord,
        inputs: tuple[ArtifactReference, ...] | None = None,
        outputs: tuple[ArtifactReference, ...] | None = None,
        *,
        bindings: Mapping[str, str | tuple[str, ...] | None] | None = None,
    ) -> None:
        object.__setattr__(
            self,
            "bindings",
            None if bindings is None else MappingProxyType(dict(bindings)),
        )
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "procedure", procedure)
        object.__setattr__(self, "inputs", () if inputs is None else inputs)
        object.__setattr__(self, "outputs", () if outputs is None else outputs)
        self.__post_init__()

    def __post_init__(self) -> None:
        _require_text(self.identity, "identity")
        if not isinstance(self.procedure, ProcedureRecord):
            raise TypeError("procedure must be a procedure record")
        if not self.inputs and not self.outputs:
            raise ValueError(
                "a procedure execution requires at least one input or output"
            )
        self._validate_references(self.inputs, "input")
        self._validate_references(self.outputs, "output")
        self._validate_edge_directions()
        object.__setattr__(self, "outputs", self._normalized(self.outputs))
        self._validate_bindings()

    def _validate_bindings(self) -> None:
        if self.bindings is None:
            return
        seen_inputs: set[str] = set()
        seen_outputs: set[str] = set()
        for key, value in self.bindings.items():
            if not isinstance(key, str):
                raise TypeError("binding name must be a string")
            scope, separator, name = key.partition(".")
            if not separator or not name or scope not in {"inputs", "setup", "outputs"}:
                raise ValueError("invalid binding name")
            if scope == "outputs":
                if not isinstance(value, str) or value in seen_outputs:
                    raise ValueError("invalid output binding")
                seen_outputs.add(value)
            else:
                values = (
                    ()
                    if value is None
                    else value
                    if isinstance(value, tuple)
                    else (value,)
                )
                if any(not isinstance(item, str) for item in values):
                    raise TypeError("invalid input binding")
                seen_inputs.update(values)
        if seen_inputs != {ref.identity for ref in self.inputs}:
            raise ValueError("input bindings do not match dependencies")
        if seen_outputs != {ref.identity for ref in self.outputs}:
            raise ValueError("output bindings do not match outputs")

    @staticmethod
    def _validate_references(
        references: tuple[ArtifactReference, ...],
        kind: str,
    ) -> None:
        if any(not isinstance(item, ArtifactReference) for item in references):
            raise TypeError(f"{kind} must contain artifact references")
        identities = [item.identity for item in references]
        if len(identities) != len(set(identities)):
            raise ValueError(f"duplicate {kind} artifact identity")

    @staticmethod
    def _normalized(
        references: tuple[ArtifactReference, ...],
    ) -> tuple[ArtifactReference, ...]:
        return tuple(sorted(references, key=lambda item: item.identity))

    def _validate_edge_directions(self) -> None:
        input_identities = {reference.identity for reference in self.inputs}
        output_identities = {reference.identity for reference in self.outputs}
        if input_identities & output_identities:
            raise ValueError("artifact cannot be both an input and an output")

    def to_dict(self) -> dict[str, Any]:
        return {
            **(
                {
                    "bindings": {
                        key: list(value) if isinstance(value, tuple) else value
                        for key, value in self.bindings.items()
                    },
                    "bindings_version": 1,
                }
                if self.bindings is not None
                else {}
            ),
            "identity": self.identity,
            "inputs": [reference.to_dict() for reference in self.inputs],
            "outputs": [reference.to_dict() for reference in self.outputs],
            "procedure": self.procedure.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        keys = {"identity", "inputs", "outputs", "procedure"}
        if "bindings" in value:
            keys |= {"bindings", "bindings_version"}
            if value.get("bindings_version") != 1:
                raise ValueError("unsupported bindings version")
        cls._expect_keys(value, keys)
        return cls(
            value["identity"],
            ProcedureRecord.from_dict(value["procedure"]),
            tuple(ArtifactReference.from_dict(item) for item in value["inputs"]),
            tuple(ArtifactReference.from_dict(item) for item in value["outputs"]),
            bindings={
                key: tuple(cast(list[Any], item)) if isinstance(item, list) else item
                for key, item in value["bindings"].items()
            }
            if "bindings" in value
            else None,
        )


@dataclass(frozen=True, slots=True)
class ArtifactLineage(_SerializableRecord):
    """A normalized, mergeable graph of artifacts and producing executions."""

    artifacts: Mapping[str, ArtifactRecord] = field(
        default_factory=dict[str, ArtifactRecord]
    )
    executions: Mapping[str, ProcedureExecutionRecord] = field(
        default_factory=dict[str, ProcedureExecutionRecord]
    )
    _shape_name: ClassVar[str] = "artifact lineage"

    def __post_init__(self) -> None:
        artifacts = dict(self.artifacts)
        executions = dict(self.executions)
        self._validate_artifacts(artifacts, executions)
        self._validate_executions(artifacts, executions)
        self._validate_acyclic(artifacts, executions)
        object.__setattr__(self, "artifacts", MappingProxyType(artifacts))
        object.__setattr__(self, "executions", MappingProxyType(executions))

    @staticmethod
    def _validate_artifacts(
        artifacts: Mapping[str, ArtifactRecord],
        executions: Mapping[str, ProcedureExecutionRecord],
    ) -> None:
        for identity, record in artifacts.items():
            if not isinstance(record, ArtifactRecord):
                raise TypeError("artifact map values must be artifact records")
            if identity != record.reference.identity:
                raise ValueError("artifact map key does not match artifact identity")
            execution = executions.get(record.producer_execution_identity)
            if execution is None:
                raise ValueError(
                    f"artifact {identity!r} has a missing producer execution"
                )
            if record.reference not in execution.outputs:
                raise ValueError(
                    f"artifact {identity!r} is not an output of its producer execution"
                )

    @staticmethod
    def _validate_executions(
        artifacts: Mapping[str, ArtifactRecord],
        executions: Mapping[str, ProcedureExecutionRecord],
    ) -> None:
        for identity, execution in executions.items():
            if not isinstance(execution, ProcedureExecutionRecord):
                raise TypeError(
                    "execution map values must be procedure execution records"
                )
            if identity != execution.identity:
                raise ValueError("execution map key does not match execution identity")
            for reference in (*execution.inputs, *execution.outputs):
                record = artifacts.get(reference.identity)
                if record is None:
                    raise ValueError(
                        f"execution references missing artifact {reference.identity!r}"
                    )
                if record.reference != reference:
                    raise ValueError(
                        f"execution reference does not match artifact "
                        f"{reference.identity!r}"
                    )

    @staticmethod
    def _validate_acyclic(
        artifacts: Mapping[str, ArtifactRecord],
        executions: Mapping[str, ProcedureExecutionRecord],
    ) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(identity: str) -> None:
            if identity in visiting:
                raise ValueError("artifact lineage contains a producer cycle")
            if identity in visited:
                return
            visiting.add(identity)
            record = artifacts[identity]
            execution = executions[record.producer_execution_identity]
            for reference in execution.inputs:
                visit(reference.identity)
            visiting.remove(identity)
            visited.add(identity)

        for identity in artifacts:
            visit(identity)

    @classmethod
    def for_execution(
        cls,
        execution: ProcedureExecutionRecord,
        output_records: tuple[ArtifactRecord, ...],
        input_lineages: tuple[ArtifactLineage, ...] = (),
    ) -> Self:
        """Extend merged input lineages with one completed execution."""
        lineage = cls._merge_many(input_lineages)
        records = cls._validated_output_records(execution, output_records)
        cls._validate_inputs(execution, lineage)

        artifacts = dict(lineage.artifacts)
        artifacts.update(records)
        executions = dict(lineage.executions)
        existing = executions.get(execution.identity)
        if existing is not None and existing != execution:
            raise ValueError(f"execution conflict for identity {execution.identity!r}")
        executions[execution.identity] = execution
        return cls(artifacts, executions)

    @classmethod
    def _merge_many(cls, lineages: tuple[ArtifactLineage, ...]) -> Self:
        result = cls()
        for lineage in lineages:
            result = result.merge(lineage)
        return result

    @staticmethod
    def _validated_output_records(
        execution: ProcedureExecutionRecord,
        output_records: tuple[ArtifactRecord, ...],
    ) -> dict[str, ArtifactRecord]:
        output_references = {
            reference.identity: reference for reference in execution.outputs
        }
        records = {record.reference.identity: record for record in output_records}
        if set(records) != set(output_references) or any(
            records[identity].reference != reference
            for identity, reference in output_references.items()
        ):
            raise ValueError("output records must exactly match execution outputs")
        if any(
            record.producer_execution_identity != execution.identity
            for record in output_records
        ):
            raise ValueError("output record producer must match the execution")
        return records

    @staticmethod
    def _validate_inputs(
        execution: ProcedureExecutionRecord,
        lineage: ArtifactLineage,
    ) -> None:
        for reference in execution.inputs:
            record = lineage.artifacts.get(reference.identity)
            if record is None or record.reference != reference:
                raise ValueError(
                    f"input artifact is absent from input lineage: {reference.identity}"
                )

    def merge(self, other: ArtifactLineage) -> Self:
        """Merge another compatible lineage into this graph."""
        artifacts = dict(self.artifacts)
        executions = dict(self.executions)
        for identity, record in other.artifacts.items():
            if identity in artifacts and artifacts[identity] != record:
                raise ValueError(f"artifact conflict for identity {identity!r}")
            artifacts[identity] = record
        for identity, execution in other.executions.items():
            if identity in executions and executions[identity] != execution:
                raise ValueError(f"execution conflict for identity {identity!r}")
            executions[identity] = execution
        return type(self)(artifacts, executions)

    def artifact(self, reference: ArtifactReference) -> ArtifactRecord:
        """Return the record matching an exact artifact reference."""
        record = self.artifacts[reference.identity]
        if record.reference != reference:
            raise ValueError("artifact reference does not match the stored artifact")
        return record

    def producing_execution(
        self,
        reference: ArtifactReference,
    ) -> ProcedureExecutionRecord:
        """Return the execution that produced an artifact."""
        record = self.artifact(reference)
        return self.executions[record.producer_execution_identity]

    def ancestry(self, reference: ArtifactReference) -> Self:
        """Return the subgraph needed to produce an artifact."""
        artifacts: dict[str, ArtifactRecord] = {}
        executions: dict[str, ProcedureExecutionRecord] = {}

        def visit(current: ArtifactReference) -> None:
            record = self.artifact(current)
            if current.identity in artifacts:
                return
            artifacts[current.identity] = record
            execution = self.executions[record.producer_execution_identity]
            executions[execution.identity] = execution
            for input_reference in execution.inputs:
                visit(input_reference)
            for output_reference in execution.outputs:
                artifacts[output_reference.identity] = self.artifact(output_reference)

        visit(reference)
        return type(self)(artifacts, executions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifacts": [
                self.artifacts[identity].to_dict()
                for identity in sorted(self.artifacts)
            ],
            "executions": [
                self.executions[identity].to_dict()
                for identity in sorted(self.executions)
            ],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        cls._expect_keys(value, {"artifacts", "executions"})
        if not isinstance(value["artifacts"], list) or not isinstance(
            value["executions"], list
        ):
            raise TypeError("invalid artifact lineage")
        artifact_values = cast(list[Mapping[str, Any]], value["artifacts"])
        execution_values = cast(list[Mapping[str, Any]], value["executions"])
        artifacts = [ArtifactRecord.from_dict(item) for item in artifact_values]
        executions = [
            ProcedureExecutionRecord.from_dict(item) for item in execution_values
        ]
        artifact_map = {record.reference.identity: record for record in artifacts}
        if len(artifact_map) != len(artifacts):
            raise ValueError("duplicate artifact in serialized lineage")
        execution_map = {execution.identity: execution for execution in executions}
        if len(execution_map) != len(executions):
            raise ValueError("duplicate execution in serialized lineage")
        return cls(artifact_map, execution_map)


__all__ = [
    "ArtifactLineage",
    "ArtifactRecord",
    "ArtifactReference",
    "ProcedureExecutionRecord",
    "ProcedureRecord",
]
