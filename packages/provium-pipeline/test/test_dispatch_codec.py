from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from provium_pipeline.attempts import RetryPolicy
from provium_pipeline.dispatch_codec import (
    DispatchCodecError,
    dispatch_document,
    dispatch_from_document,
    dispatch_from_json,
    dispatch_json,
)
from provium_pipeline.dispatch_models import (
    DependencyPolicy,
    Dispatch,
    DispatchState,
    TaskSelection,
)
from provium_pipeline.identifiers import DispatchId, RunId, TaskId


def _dispatch() -> Dispatch:
    return Dispatch(
        identifier=DispatchId.parse("00000000-0000-0000-0000-000000000001"),
        run_identifier=RunId.parse("00000000-0000-0000-0000-000000000002"),
        selection=TaskSelection(
            all_remaining=True,
            labels=("slow", "fast"),
            nodes=("second", "first"),
            only_failed=True,
            procedures=("clean",),
            records=("record-b", "record-a"),
            only_incomplete=False,
        ),
        task_identifiers=(
            TaskId.parse("00000000-0000-0000-0000-000000000004"),
            TaskId.parse("00000000-0000-0000-0000-000000000003"),
        ),
        dependency_policy=DependencyPolicy.SELECTED_ONLY,
        retry_policy=RetryPolicy(
            max_attempts=7,
            initial_backoff=timedelta(milliseconds=250),
            multiplier=1.5,
            max_backoff=timedelta(seconds=9),
        ),
        state=DispatchState.FAILED,
        created_at=datetime(2026, 1, 2, 3, 4, 5, 6000, tzinfo=UTC),
        terminal_at=datetime(2026, 1, 2, 3, 5, 6, 7000, tzinfo=UTC),
        idempotency_key="stable-key",
    )


def test_dispatch_codec_round_trips_every_durable_field_canonically() -> None:
    dispatch = _dispatch()

    document = dispatch_document(dispatch)
    encoded = dispatch_json(dispatch)

    assert document["schema"] == "provium.pipeline.dispatch"
    assert document["version"] == 1
    assert encoded == dispatch_json(dispatch_from_document(document))
    assert dispatch_from_json(encoded) == dispatch
    assert encoded.endswith("\n")
    assert " " not in encoded


def test_dispatch_codec_preserves_optional_values_and_utc_normalization() -> None:
    dispatch = Dispatch(
        identifier=DispatchId.parse("00000000-0000-0000-0000-000000000011"),
        run_identifier=RunId.parse("00000000-0000-0000-0000-000000000012"),
        selection=TaskSelection(all=True),
        task_identifiers=(),
        dependency_policy=DependencyPolicy.INCLUDE_MISSING_UPSTREAM,
        retry_policy=RetryPolicy(max_attempts=1),
        state=DispatchState.CREATED,
        created_at=datetime.fromisoformat("2026-01-02T03:04:05+02:00"),
    )

    restored = dispatch_from_json(dispatch_json(dispatch))

    assert restored == dispatch
    assert dispatch_document(dispatch)["dispatch"]["created_at"] == (
        "2026-01-02T01:04:05.000000Z"
    )


@pytest.mark.parametrize(
    "document",
    (
        {},
        {"schema": "wrong", "version": 1, "dispatch": {}},
        {"schema": "provium.pipeline.dispatch", "version": 2, "dispatch": {}},
        {"schema": "provium.pipeline.dispatch", "version": 1, "dispatch": []},
    ),
)
def test_dispatch_codec_rejects_invalid_envelopes(document: object) -> None:
    with pytest.raises(DispatchCodecError):
        dispatch_from_document(document)


def test_dispatch_codec_wraps_invalid_json_and_payload_values() -> None:
    with pytest.raises(DispatchCodecError, match="JSON"):
        dispatch_from_json("not-json")

    document = dispatch_document(_dispatch())
    document["dispatch"]["state"] = "unknown"
    with pytest.raises(DispatchCodecError, match="payload"):
        dispatch_from_document(document)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    (
        ("payload", "identifier", 1),
        ("selection", "all", "yes"),
        ("retry", "max_attempts", True),
        ("retry", "multiplier", True),
        ("payload", "task_identifiers", "not-an-array"),
        ("payload", "task_identifiers", [1]),
        ("payload", "idempotency_key", 1),
        ("payload", "terminal_at", 1),
        ("payload", "created_at", "not-a-timestamp"),
    ),
)
def test_dispatch_codec_rejects_malformed_payload_types(
    section: str,
    field: str,
    value: object,
) -> None:
    document = dispatch_document(_dispatch())
    payload = cast(dict[str, object], document["dispatch"])
    sections = {
        "payload": payload,
        "selection": cast(dict[str, object], payload["selection"]),
        "retry": cast(dict[str, object], payload["retry_policy"]),
    }
    sections[section][field] = value

    with pytest.raises(DispatchCodecError, match="payload"):
        dispatch_from_document(document)


def test_dispatch_codec_rejects_non_string_object_keys() -> None:
    with pytest.raises(DispatchCodecError, match="object"):
        dispatch_from_document(cast(object, {1: "invalid"}))
