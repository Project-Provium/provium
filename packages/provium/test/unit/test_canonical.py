"""Tests for public canonical JSON utilities."""

from __future__ import annotations

from hashlib import sha256

import pytest

from provium import canonical_digest, canonical_json, canonical_json_bytes


def test_canonical_json_is_compact_utf8_with_sorted_mapping_keys() -> None:
    value = {"z": [True, None, 2.5], "a": {"é": "snowman ☃"}}

    encoded = canonical_json_bytes(value)

    assert encoded == '{"a":{"é":"snowman ☃"},"z":[true,null,2.5]}'.encode()
    assert canonical_json(value) == encoded.decode()
    assert canonical_digest(value) == sha256(encoded).hexdigest()


def test_canonical_json_preserves_sequence_order() -> None:
    assert canonical_json(["second", "first"]) == '["second","first"]'


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonical_json_rejects_nonfinite_numbers(value: float) -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        canonical_json(value)


def test_canonical_json_rejects_values_outside_the_json_domain() -> None:
    with pytest.raises(TypeError):
        canonical_json(object())  # type: ignore[arg-type]
