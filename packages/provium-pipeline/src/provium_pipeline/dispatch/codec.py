import json
from datetime import UTC, datetime, timedelta
from typing import TypedDict, cast

from provium_pipeline.dispatch.models import (
    DependencyPolicy,
    Dispatch,
    DispatchState,
    TaskSelection,
)
from provium_pipeline.execution.attempts import RetryPolicy
from provium_pipeline.identifiers import DispatchId, RunId, TaskId

_SCHEMA = "provium.pipeline.dispatch"
_VERSION = 1


class DispatchCodecError(ValueError):
    """Raised when a durable dispatch document cannot be decoded."""


class _SelectionDocument(TypedDict):
    all: bool
    all_remaining: bool
    labels: list[str]
    nodes: list[str]
    only_failed: bool
    procedures: list[str]
    records: list[str]
    only_incomplete: bool


class _RetryPolicyDocument(TypedDict):
    max_attempts: int
    initial_backoff_seconds: float
    multiplier: float
    max_backoff_seconds: float


class _DispatchDocument(TypedDict):
    identifier: str
    run_identifier: str
    selection: _SelectionDocument
    task_identifiers: list[str]
    dependency_policy: str
    retry_policy: _RetryPolicyDocument
    state: str
    created_at: str
    terminal_at: str | None
    idempotency_key: str | None


class DispatchEnvelope(TypedDict):
    schema: str
    version: int
    dispatch: _DispatchDocument


def dispatch_document(dispatch: Dispatch) -> DispatchEnvelope:
    """Return the versioned canonical JSON-domain representation of a dispatch."""
    selection = dispatch.selection
    retry_policy = dispatch.retry_policy
    return {
        "schema": _SCHEMA,
        "version": _VERSION,
        "dispatch": {
            "identifier": str(dispatch.identifier),
            "run_identifier": str(dispatch.run_identifier),
            "selection": {
                "all": selection.all,
                "all_remaining": selection.all_remaining,
                "labels": list(selection.labels),
                "nodes": list(selection.nodes),
                "only_failed": selection.only_failed,
                "procedures": list(selection.procedures),
                "records": list(selection.records),
                "only_incomplete": selection.only_incomplete,
            },
            "task_identifiers": [str(value) for value in dispatch.task_identifiers],
            "dependency_policy": dispatch.dependency_policy.value,
            "retry_policy": {
                "max_attempts": retry_policy.max_attempts,
                "initial_backoff_seconds": retry_policy.initial_backoff.total_seconds(),
                "multiplier": retry_policy.multiplier,
                "max_backoff_seconds": retry_policy.max_backoff.total_seconds(),
            },
            "state": dispatch.state.value,
            "created_at": _timestamp(dispatch.created_at),
            "terminal_at": (
                _timestamp(dispatch.terminal_at)
                if dispatch.terminal_at is not None
                else None
            ),
            "idempotency_key": dispatch.idempotency_key,
        },
    }


def dispatch_json(dispatch: Dispatch) -> str:
    """Serialize a dispatch as stable compact UTF-8 JSON text."""
    return (
        json.dumps(
            dispatch_document(dispatch),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def dispatch_from_json(value: str) -> Dispatch:
    """Decode a dispatch from its versioned JSON representation."""
    try:
        document = cast(object, json.loads(value))
    except json.JSONDecodeError as error:
        raise DispatchCodecError("invalid dispatch JSON") from error
    return dispatch_from_document(document)


def dispatch_from_document(document: object) -> Dispatch:
    """Decode and validate a versioned dispatch document."""
    envelope = _object_dict(document)
    if envelope.get("schema") != _SCHEMA or envelope.get("version") != _VERSION:
        raise DispatchCodecError("unsupported dispatch schema or version")
    payload = _object_dict(envelope.get("dispatch"))
    try:
        return _dispatch_from_payload(payload)
    except (KeyError, TypeError, ValueError) as error:
        raise DispatchCodecError("invalid dispatch payload") from error


def _dispatch_from_payload(payload: dict[str, object]) -> Dispatch:
    return Dispatch(
        identifier=DispatchId.parse(_string(payload["identifier"])),
        run_identifier=RunId.parse(_string(payload["run_identifier"])),
        selection=_selection_from_value(payload["selection"]),
        task_identifiers=tuple(
            TaskId.parse(value) for value in _strings(payload["task_identifiers"])
        ),
        dependency_policy=DependencyPolicy(_string(payload["dependency_policy"])),
        retry_policy=_retry_policy_from_value(payload["retry_policy"]),
        state=DispatchState(_string(payload["state"])),
        created_at=_datetime(payload["created_at"]),
        terminal_at=_optional_datetime(payload["terminal_at"]),
        idempotency_key=_optional_string(payload["idempotency_key"]),
    )


def _selection_from_value(value: object) -> TaskSelection:
    selection = _object_dict(value)
    return TaskSelection(
        all=_boolean(selection["all"]),
        all_remaining=_boolean(selection["all_remaining"]),
        labels=_strings(selection["labels"]),
        nodes=_strings(selection["nodes"]),
        only_failed=_boolean(selection["only_failed"]),
        procedures=_strings(selection["procedures"]),
        records=_strings(selection["records"]),
        only_incomplete=_boolean(selection["only_incomplete"]),
    )


def _retry_policy_from_value(value: object) -> RetryPolicy:
    retry_policy = _object_dict(value)
    return RetryPolicy(
        max_attempts=_integer(retry_policy["max_attempts"]),
        initial_backoff=timedelta(
            seconds=_number(retry_policy["initial_backoff_seconds"])
        ),
        multiplier=_number(retry_policy["multiplier"]),
        max_backoff=timedelta(seconds=_number(retry_policy["max_backoff_seconds"])),
    )


def _object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise DispatchCodecError("expected JSON object")
    mapping = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise DispatchCodecError("expected JSON object")
    return cast(dict[str, object], mapping)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected string")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string(value)


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError("expected boolean")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("expected integer")
    return value


def _number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError("expected number")
    return float(value)


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError("expected string array")
    items = cast(list[object], value)
    if not all(isinstance(item, str) for item in items):
        raise TypeError("expected string array")
    return tuple(cast(list[str], items))


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(_string(value).replace("Z", "+00:00"))


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return _datetime(value)


def _timestamp(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


__all__ = [
    "DispatchCodecError",
    "DispatchEnvelope",
    "dispatch_document",
    "dispatch_from_document",
    "dispatch_from_json",
    "dispatch_json",
]
