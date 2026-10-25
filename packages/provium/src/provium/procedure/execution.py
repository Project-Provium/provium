"""Low-level procedure execution sessions and output provenance."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from uuid import uuid4

from provium.artifact import (
    ArtifactHeader,
    ArtifactWriteBinding,
    ArtifactWriter,
    StagedArtifact,
    stage_artifact,
)
from provium.files import local_file, location_identity, publish_file, remove_file
from provium.procedure.result import ProcedureExecutionResult
from provium.provenance import (
    ArtifactLineage,
    ArtifactRecord,
    ArtifactReference,
    ProcedureExecutionRecord,
    ProcedureRecord,
)
from provium.session import Session, current_session, session

_EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()


class ProcedureExecutionSession:
    """Own one invocation's child session, staged outputs, and lineage."""

    def __init__(self, procedure: ProcedureRecord) -> None:
        if not isinstance(procedure, ProcedureRecord):
            raise TypeError("procedure must be a ProcedureRecord")
        self.identity = str(uuid4())
        self.procedure = procedure
        self.bindings: dict[str, str | tuple[str, ...] | None] | None = None
        self._session: Session | None = None
        self._staged: dict[str, StagedArtifact[Any]] | None = None
        self._references: dict[str, ArtifactReference] = {}
        self._lineage: ArtifactLineage | None = None
        self._result: ProcedureExecutionResult | None = None
        self._used = False
        self._remote_attempts: set[str] = set()

    @property
    def writers(self) -> tuple[ArtifactWriter, ...]:
        """Return staged writers in declaration order."""
        if self._staged is None:
            return ()
        return tuple(output.writer for output in self._staged.values())

    @property
    def outputs(self) -> tuple[ArtifactReference, ...]:
        """Return output references in declaration order."""
        return tuple(self._references.values())

    @property
    def lineage(self) -> ArtifactLineage | None:
        """Return finalized execution lineage after successful publication."""
        return self._lineage

    @property
    def result(self) -> ProcedureExecutionResult | None:
        """Return immutable metadata after successful publication."""
        return self._result

    def __enter__(self) -> Self:
        if self._used:
            raise RuntimeError("procedure execution session has already been entered")
        if current_session() is None:
            raise RuntimeError("procedure execution requires an active session")
        self._used = True
        child = session()
        child.__enter__()
        self._session = child
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        child = self._require_active()
        if exc_type is not None:
            child.__exit__(exc_type, exc_value, traceback)
            return
        try:
            self._finalize_outputs(child)
        except BaseException as error:
            child.__exit__(type(error), error, error.__traceback__)
            raise
        child.__exit__(None, None, None)

    def stage_outputs(
        self,
        bindings: Mapping[str, ArtifactWriteBinding[Any]],
    ) -> dict[str, ArtifactWriter]:
        """Stage every output together using one provisional execution graph."""
        child = self._require_active()
        if self._staged is not None:
            raise RuntimeError("procedure outputs have already been staged")
        if not bindings:
            raise ValueError("procedure execution requires at least one output")
        self._validate_destinations(bindings)
        references = {
            name: ArtifactReference(
                str(uuid4()),
                binding.artifact.definition.identifier,
            )
            for name, binding in bindings.items()
        }
        provisional_execution = self._execution_record(child, references)
        provisional_records = tuple(
            ArtifactRecord(reference, _EMPTY_DIGEST, self.identity)
            for reference in references.values()
        )
        provisional_lineage = self._lineage_for(
            child,
            provisional_execution,
            provisional_records,
        )
        staged: dict[str, StagedArtifact[Any]] = {}
        for name, binding in bindings.items():
            reference = references[name]
            metadata = ArtifactHeader.create(
                artifact_identifier=reference.artifact_identifier,
                artifact_identity=reference.identity,
                body_length=0,
                body_digest=_EMPTY_DIGEST,
                lineage=provisional_lineage,
            )
            staged[name] = stage_artifact(binding, metadata, child)
        self._references = references
        self._staged = staged
        return {name: output.writer for name, output in staged.items()}

    def _require_active(self) -> Session:
        child = self._session
        if child is None or current_session() is not child or not child.active:
            raise RuntimeError("procedure execution session is not active")
        return child

    @staticmethod
    def _validate_destinations(
        bindings: Mapping[str, ArtifactWriteBinding[Any]],
    ) -> None:
        destinations = [
            location_identity(binding.path) for binding in bindings.values()
        ]
        if len(destinations) != len(set(destinations)):
            raise ValueError("procedure output destination is already in use")

    def _execution_record(
        self,
        child: Session,
        references: Mapping[str, ArtifactReference],
    ) -> ProcedureExecutionRecord:
        if self.bindings is None and not child.inputs:
            self.bindings = {}
        return ProcedureExecutionRecord(
            self.identity,
            self.procedure,
            tuple(record.reference for record in child.inputs),
            tuple(references.values()),
            bindings=(
                {
                    **self.bindings,
                    **{
                        f"outputs.{name}": ref.identity
                        for name, ref in references.items()
                    },
                }
                if self.bindings is not None
                else None
            ),
        )

    @staticmethod
    def _lineage_for(
        child: Session,
        execution: ProcedureExecutionRecord,
        records: tuple[ArtifactRecord, ...],
    ) -> ArtifactLineage:
        input_lineages = (child.input_lineage,) if child.inputs else ()
        return ArtifactLineage.for_execution(execution, records, input_lineages)

    def _finalize_outputs(self, child: Session) -> None:
        if self._staged is None:
            raise RuntimeError("procedure outputs have not been staged")
        summaries = {
            name: output.finalize_body() for name, output in self._staged.items()
        }
        records = tuple(
            ArtifactRecord(
                self._references[name],
                summaries[name][1],
                self.identity,
            )
            for name in self._staged
        )
        execution = self._execution_record(child, self._references)
        lineage = self._lineage_for(child, execution, records)
        records_by_identity = {record.reference.identity: record for record in records}
        metadata_by_name: dict[str, ArtifactHeader] = {}
        for name in self._staged:
            reference = self._references[name]
            record = records_by_identity[reference.identity]
            metadata_by_name[name] = ArtifactHeader.create(
                artifact_identifier=reference.artifact_identifier,
                artifact_identity=reference.identity,
                body_length=summaries[name][0],
                body_digest=record.body_digest,
                lineage=lineage,
            )
        self._publish_outputs(metadata_by_name)
        self._lineage = lineage
        self._result = ProcedureExecutionResult(
            execution.identity,
            execution.procedure,
            execution.inputs,
            {name: self._references[name] for name in self._staged},
            lineage,
        )

    def _publish_outputs(self, metadata: Mapping[str, ArtifactHeader]) -> None:
        assert self._staged is not None
        backups: dict[str, Path | None] = {}
        cleanup = ExitStack()
        self._require_active().manage(cleanup)
        try:
            for name, output in self._staged.items():
                destination = output.destination
                backup = None
                if isinstance(destination, str):
                    try:
                        backup = cleanup.enter_context(local_file(destination))
                    except FileNotFoundError:
                        pass
                elif destination.exists():
                    backup = destination.with_name(
                        f".{destination.name}.{uuid4().hex}.backup"
                    )
                    destination.replace(backup)
                backups[name] = backup
            for name, output in self._staged.items():
                if isinstance(output.destination, str):
                    self._remote_attempts.add(name)
                output.publish(metadata[name])
        except BaseException as error:
            try:
                self._restore_destinations(backups)
            except BaseException as restore_error:
                raise error from restore_error
            raise
        for backup in backups.values():
            if backup is not None:
                backup.unlink(missing_ok=True)

    def _restore_destinations(
        self,
        backups: Mapping[str, Path | None],
    ) -> None:
        assert self._staged is not None
        for name, output in reversed(tuple(self._staged.items())):
            if isinstance(output.destination, str):
                if name in self._remote_attempts:
                    backup = backups[name]
                    if backup is None:
                        remove_file(output.destination)
                    else:
                        publish_file(backup, output.destination)
                continue
            if output.published:
                output.destination.unlink(missing_ok=True)
            backup = backups.get(name)
            if backup is not None and backup.exists():
                backup.replace(output.destination)


__all__ = ["ProcedureExecutionSession"]
