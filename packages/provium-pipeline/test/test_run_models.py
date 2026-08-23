from __future__ import annotations

from provium import canonical_digest
from provium_pipeline import InputRecord, RunInputSnapshot, TaskState, run_fingerprint
from provium_pipeline.identifiers import InputRecordKey
from test.compiler.test_compiler_success import compiler, definition


def input_snapshot(identity: str) -> RunInputSnapshot:
    return RunInputSnapshot.create(
        records=(
            InputRecord(
                key=InputRecordKey("record-1"),
                inputs={"source": (identity,)},
                labels={},
            ),
        ),
        shared_inputs={"model": ("model-1",)},
        sources=(),
    )


def test_run_fingerprint_contains_only_three_semantic_digests() -> None:
    compiled = compiler().compile(definition())
    inputs = input_snapshot("source-1")

    fingerprint = run_fingerprint(compiled, inputs)

    assert fingerprint == canonical_digest(
        {
            "pipeline": compiled.semantic_digest,
            "configuration": compiled.resolved_configuration.document_digest,
            "inputs": inputs.digest,
        }
    )
    assert fingerprint != run_fingerprint(compiled, input_snapshot("source-2"))


def test_task_state_satisfaction_is_explicit() -> None:
    assert TaskState.SUCCEEDED.satisfied
    assert TaskState.REUSED.satisfied
    assert not TaskState.READY.satisfied
