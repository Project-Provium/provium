"""Canonical JSON encoding and stable SHA-256 identities."""

from __future__ import annotations

import json
from hashlib import sha256

from .procedure.config import JsonValue


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Encode a JSON-domain value deterministically as UTF-8."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_json(value: JsonValue) -> str:
    """Encode a JSON-domain value deterministically as text."""

    return canonical_json_bytes(value).decode("utf-8")


def canonical_digest(value: JsonValue) -> str:
    """Return the SHA-256 hex digest of canonical JSON bytes."""

    return sha256(canonical_json_bytes(value)).hexdigest()


__all__ = ["canonical_digest", "canonical_json", "canonical_json_bytes"]
