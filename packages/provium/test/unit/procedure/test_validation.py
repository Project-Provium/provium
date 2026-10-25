"""Tests for procedure-aware configuration validation."""

from collections.abc import Mapping
from typing import assert_type

import pytest
from pydantic import Field, field_validator

from provium import (
    ProcedureConfig,
    ProcedureConfigurationError,
    validate_procedure_configuration,
)


class DetectionConfig(ProcedureConfig):
    model: str
    confidence: float = Field(default=0.5, ge=0, le=1)

    @field_validator("model")
    @classmethod
    def assert_model_name_is_valid(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model must be nonempty")
        return value


def test_validation_returns_the_exact_configuration_type() -> None:
    configuration = validate_procedure_configuration(
        "example.DetectV1",
        DetectionConfig,
        {"model": "detector"},
    )

    assert_type(configuration, DetectionConfig)
    assert configuration == DetectionConfig(model="detector", confidence=0.5)


def test_validation_does_not_mutate_raw_values() -> None:
    values: Mapping[str, object] = {"model": "detector"}

    validate_procedure_configuration("example.DetectV1", DetectionConfig, values)

    assert values == {"model": "detector"}


def test_validation_error_preserves_procedure_and_pydantic_context() -> None:
    with pytest.raises(ProcedureConfigurationError) as raised:
        validate_procedure_configuration(
            "example.DetectV1",
            DetectionConfig,
            {"model": "", "confidence": 2, "unknown": True},
            source_layers=["defaults.yaml", "experiment.json"],
        )

    error = raised.value
    assert error.procedure_identifier == "example.DetectV1"
    assert error.model_target == f"{__name__}:DetectionConfig"
    assert error.source_layers == ("defaults.yaml", "experiment.json")
    assert {detail["type"] for detail in error.errors()} == {
        "extra_forbidden",
        "less_than_equal",
        "value_error",
    }
    assert {detail["loc"] for detail in error.errors()} == {
        ("confidence",),
        ("model",),
        ("unknown",),
    }
    assert error.validation_error.errors() == error.errors()
    assert error.__cause__ is error.validation_error


def test_validation_error_message_identifies_fields_and_sources() -> None:
    with pytest.raises(ProcedureConfigurationError) as raised:
        validate_procedure_configuration(
            "example.DetectV1",
            DetectionConfig,
            {},
            source_layers=(name for name in ["defaults.yaml", "local.yaml"]),
        )

    message = str(raised.value)
    assert "example.DetectV1" in message
    assert f"{__name__}:DetectionConfig" in message
    assert "model" in message
    assert "missing" in message
    assert "defaults.yaml, local.yaml" in message


def test_validation_error_without_sources_omits_source_text() -> None:
    with pytest.raises(ProcedureConfigurationError) as raised:
        validate_procedure_configuration("example.DetectV1", DetectionConfig, {})

    assert "sources:" not in str(raised.value)
