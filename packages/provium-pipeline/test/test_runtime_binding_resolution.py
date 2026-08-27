from __future__ import annotations

import pytest

from provium_pipeline.compiler.models import CompiledBindingPlan
from provium_pipeline.identifiers import InputRecordKey
from provium_pipeline.input.models import InputRecord
from provium_pipeline.task_executor import (
    BindingResolutionError,
    resolve_runtime_binding_identities,
)


def _plan(*references: str, minimum: int = 1, maximum: int | None = None):
    return CompiledBindingPlan(
        field="images",
        artifact_identifier="example/image@1",
        minimum=minimum,
        maximum=maximum,
        references=references,
    )


def test_runtime_binding_expands_ordered_record_and_upstream_references() -> None:
    record = InputRecord(
        key=InputRecordKey("record-1"),
        inputs={"images": ("image-1", "image-2", "image-3")},
        labels={},
    )

    identities = resolve_runtime_binding_identities(
        _plan("$inputs.images", "$nodes.decode.preview"),
        record=record,
        shared_inputs={},
        resolve_upstream=lambda reference: (
            ("preview-1",) if reference == "$nodes.decode.preview" else ()
        ),
    )

    assert identities == ("image-1", "image-2", "image-3", "preview-1")


def test_runtime_binding_falls_back_to_shared_inputs_and_allows_optional_absence() -> (
    None
):
    record = InputRecord(key=InputRecordKey("record-1"), inputs={}, labels={})

    assert resolve_runtime_binding_identities(
        _plan("$inputs.model", minimum=0, maximum=1),
        record=record,
        shared_inputs={"model": ("model-1",)},
        resolve_upstream=lambda _: (),
    ) == ("model-1",)
    assert (
        resolve_runtime_binding_identities(
            _plan("$nodes.decode.preview", minimum=0, maximum=1),
            record=record,
            shared_inputs={},
            resolve_upstream=lambda _: (),
        )
        == ()
    )


def test_runtime_binding_enforces_frozen_minimum_and_maximum() -> None:
    record = InputRecord(
        key=InputRecordKey("record-1"),
        inputs={"images": ("image-1", "image-2")},
        labels={},
    )

    with pytest.raises(BindingResolutionError, match="unsupported reference"):
        resolve_runtime_binding_identities(
            _plan("invalid", minimum=0),
            record=record,
            shared_inputs={},
            resolve_upstream=lambda _: (),
        )
    with pytest.raises(BindingResolutionError, match="requires at least 1"):
        resolve_runtime_binding_identities(
            _plan("$inputs.missing"),
            record=record,
            shared_inputs={},
            resolve_upstream=lambda _: (),
        )
    with pytest.raises(BindingResolutionError, match="accepts at most 1"):
        resolve_runtime_binding_identities(
            _plan("$inputs.images", maximum=1),
            record=record,
            shared_inputs={},
            resolve_upstream=lambda _: (),
        )
