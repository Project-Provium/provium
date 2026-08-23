from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from provium import JsonValue

from .identifiers import InputRecordKey
from .inputs import InputRecord


class InputRecordDecodeError(ValueError):
    """An NDJSON input record could not be decoded or validated."""


class _InputRecordDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    key: str = Field(min_length=1)
    inputs: dict[str, list[str]]
    labels: dict[str, JsonValue]


def load_input_records_ndjson(
    text: str,
    *,
    source: str = "<input>",
) -> tuple[InputRecord, ...]:
    """Decode source-aware NDJSON into ordered immutable input records."""
    records: list[InputRecord] = []
    keys: set[InputRecordKey] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw: Any = json.loads(line)
        except json.JSONDecodeError as error:
            raise InputRecordDecodeError(
                f"{source}:{line_number}: invalid JSON: {error.msg}"
            ) from error
        try:
            document = _InputRecordDocument.model_validate(raw)
            key = InputRecordKey(document.key)
        except (ValidationError, ValueError) as error:
            fields = _error_fields(error)
            raise InputRecordDecodeError(
                f"{source}:{line_number}: invalid input record ({fields})"
            ) from error
        if key in keys:
            raise InputRecordDecodeError(
                f"{source}:{line_number}: duplicate input record key: {key}"
            )
        keys.add(key)
        records.append(
            InputRecord(
                key=key,
                inputs=document.inputs,
                labels=document.labels,
            )
        )
    return tuple(records)


def _error_fields(error: ValidationError | ValueError) -> str:
    if isinstance(error, ValidationError):
        return ", ".join(
            ".".join(str(part) for part in detail["loc"])
            for detail in error.errors()
        )
    return "key"


__all__ = ["InputRecordDecodeError", "load_input_records_ndjson"]
