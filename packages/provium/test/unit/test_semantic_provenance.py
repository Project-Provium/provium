"""Stable provenance ignores execution identities but preserves graph meaning."""

import pytest

from provium.provenance import (
    ArtifactLineage,
    ArtifactRecord,
    ArtifactReference,
    ProcedureExecutionRecord,
    ProcedureRecord,
)
from provium.semantic import artifact_fingerprint, computation_key, output_key


def source(identity="a", digest="abc", config=None):
    ref = ArtifactReference(identity, "text@1")
    procedure = ProcedureRecord("source", "1", config)
    execution = ProcedureExecutionRecord(
        "execution-" + identity,
        procedure,
        outputs=(ref,),
        bindings={"outputs.result": identity},
    )
    record = ArtifactRecord(ref, digest, execution.identity)
    return ref, ArtifactLineage.for_execution(execution, (record,))


def test_stable_across_execution_identities_and_serialization():
    a, first = source("a")
    b, second = source("b")
    second = ArtifactLineage.from_json(second.to_json())
    assert artifact_fingerprint(first, a) == artifact_fingerprint(second, b)


def test_content_and_provenance_both_participate_downstream():
    a, first = source()
    b, changed = source("b", digest="different")
    c, other = source("c", config={"algorithm": 2})
    p = ProcedureRecord("next", "1")
    keys = {
        computation_key(p, {"source": artifact_fingerprint(lineage, ref)}, {})
        for ref, lineage in [(a, first), (b, changed), (c, other)]
    }
    assert len(keys) == 3
    assert (
        artifact_fingerprint(first, a)["provenance"]
        == artifact_fingerprint(changed, b)["provenance"]
    )


def test_named_repeated_collection_optional_and_setup_bindings():
    ref, lineage = source()
    value = artifact_fingerprint(lineage, ref)
    p = ProcedureRecord("next", "1")
    assert computation_key(p, {"left": value, "right": value}, {}) != computation_key(
        p, {"left": value}, {}
    )
    assert computation_key(p, {"items": [value, value]}, {}) != computation_key(
        p, {"items": [value]}, {}
    )
    assert computation_key(p, {"optional": None}, {}) != computation_key(p, {}, {})
    assert computation_key(p, {}, {"model": value}) != computation_key(
        p, {"model": value}, {}
    )
    assert output_key("key", "left", "text@1") != output_key("key", "right", "text@1")


def test_legacy_is_readable_but_not_verifiable():
    ref = ArtifactReference("a", "text@1")
    execution = ProcedureExecutionRecord(
        "e", ProcedureRecord("source", "1"), outputs=(ref,)
    )
    lineage = ArtifactLineage.for_execution(
        execution, (ArtifactRecord(ref, "abc", "e"),)
    )
    restored = ArtifactLineage.from_json(lineage.to_json())
    with pytest.raises(ValueError, match="binding"):
        artifact_fingerprint(restored, ref)


@pytest.mark.parametrize(
    "bindings",
    [
        {"inputs.bad": "output", "outputs.result": "output"},
        {"outputs.result": ["output"]},
        {"unknown.result": "output"},
        {"outputs.result": "missing"},
        {},
    ],
)
def test_invalid_bindings_rejected_at_record_boundary(bindings):
    with pytest.raises((TypeError, ValueError), match="binding"):
        ProcedureExecutionRecord(
            "e",
            ProcedureRecord("source", "1"),
            outputs=(ArtifactReference("output", "text@1"),),
            bindings=bindings,
        )


def test_reject_nonfinite_configuration():
    with pytest.raises(ValueError):
        computation_key(ProcedureRecord("source", "1", {"x": float("nan")}), {}, {})


@pytest.mark.parametrize(
    "bindings",
    [
        {1: "output"},
        {"inputs.bad": [1], "outputs.result": "output"},
    ],
)
def test_bindings_reject_invalid_types(bindings):
    with pytest.raises(TypeError, match="binding"):
        ProcedureExecutionRecord(
            "e",
            ProcedureRecord("source", "1"),
            outputs=(ArtifactReference("output", "text@1"),),
            bindings=bindings,
        )


def test_binding_schema_version_is_checked():
    ref, lineage = source()
    value = lineage.producing_execution(ref).to_dict()
    value["bindings_version"] = 999
    with pytest.raises(ValueError, match="version"):
        ProcedureExecutionRecord.from_dict(value)


def test_reconstruct_join_with_repeated_optional_and_setup_inputs():
    ref, lineage = source()
    outputs = (
        ArtifactReference("left", "text@1"),
        ArtifactReference("right", "text@1"),
    )
    procedure = ProcedureRecord("join", "1")
    execution = ProcedureExecutionRecord(
        "joined",
        procedure,
        (ref,),
        outputs,
        bindings={
            "inputs.items": (ref.identity, ref.identity),
            "inputs.optional": None,
            "setup.model": ref.identity,
            "outputs.left": "left",
            "outputs.right": "right",
        },
    )
    joined = ArtifactLineage.for_execution(
        execution,
        tuple(ArtifactRecord(output, "result", "joined") for output in outputs),
        (lineage,),
    )
    fingerprint = artifact_fingerprint(lineage, ref)
    expected = computation_key(
        procedure,
        {"items": [fingerprint, fingerprint], "optional": None},
        {"model": fingerprint},
    )
    assert artifact_fingerprint(joined, outputs[0])["provenance"] == output_key(
        expected, "left", "text@1"
    )
    assert artifact_fingerprint(joined, outputs[1])["provenance"] == output_key(
        expected, "right", "text@1"
    )


def test_no_declared_inputs_have_no_binding_metadata():
    from provium.procedure.prepared import PreparedProcedure

    assert PreparedProcedure._named_input_identities("inputs", None, {}) == {}
