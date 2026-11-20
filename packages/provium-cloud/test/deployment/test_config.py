"""Unnamed deployment validation and per-task scheduling resolution."""

import pytest
from pydantic import ValidationError

import provium_cloud
from provium_cloud.deployment.config import CloudDeployment, SchedulingOptions


def test_public_deployment_exports():
    from provium_cloud import deployment

    assert (
        provium_cloud.CloudDeployment is deployment.CloudDeployment is CloudDeployment
    )
    assert provium_cloud.SchedulingOptions is SchedulingOptions


@pytest.fixture
def configuration():
    return {
        "kind": "CloudDeployment",
        "provider": "aws",
        "region": "us-east-1",
        "artifacts": {"backend": "s3", "options": {"location": "s3://artifacts"}},
        "state": {
            "backend": "postgres",
            "options": {"connection_env": "PROVIUM_DATABASE_URL"},
        },
        "defaults": {
            "job_queue": "cpu",
            "job_definition": "worker:12",
            "timeout": "30m",
        },
    }


def test_resolve_defaults_and_round_trip(configuration):
    deployment = CloudDeployment.model_validate(configuration)
    settings = deployment.resolve(procedure="example.TokenizeV1")
    assert settings.model_dump() == {
        "job_queue": "cpu",
        "job_definition": "worker:12",
        "timeout": 1800,
    }
    assert "name" not in deployment.model_dump()
    assert (
        CloudDeployment.model_validate_json(deployment.model_dump_json()) == deployment
    )


def test_routes_apply_in_order_then_explicit_overrides(configuration):
    configuration["routes"] = [
        {"match": "nodes.tokenize", "job_queue": "node-queue"},
        {"match": "tags.gpu", "job_queue": "gpu", "job_definition": "gpu-worker:8"},
        {"match": "procedures.example.TokenizeV1", "timeout": "5m"},
        {"match": "tags.gpu", "job_queue": "later-gpu"},
        {"match": "nodes.other", "job_queue": "unmatched"},
    ]
    deployment = CloudDeployment.model_validate(configuration)
    before = deployment.model_dump_json()
    settings = deployment.resolve(
        procedure="example.TokenizeV1",
        node="tokenize",
        tags=iter(["gpu"]),
        overrides={"job_queue": "priority"},
    )
    assert settings.model_dump() == {
        "job_queue": "priority",
        "job_definition": "gpu-worker:8",
        "timeout": 300,
    }
    assert (
        deployment.resolve(
            procedure="example.TokenizeV1", node="tokenize", tags=["gpu"]
        ).job_queue
        == "later-gpu"
    )
    assert deployment.model_dump_json() == before


def test_standalone_procedure_routes_and_timeout_inheritance(configuration):
    configuration["routes"] = [
        {"match": "nodes.tokenize", "job_queue": "node-queue"},
        {"match": "procedures.example.TokenizeV1", "job_queue": "procedure-queue"},
    ]
    deployment = CloudDeployment.model_validate(configuration)
    settings = deployment.resolve(
        procedure="example.TokenizeV1", overrides=SchedulingOptions(timeout=None)
    )
    assert settings.job_queue == "procedure-queue"
    assert settings.timeout == 1800
    assert deployment.resolve(procedure="example.OtherV1").job_queue == "cpu"


def test_required_settings_can_come_from_routes_or_overrides(configuration):
    configuration.pop("defaults")
    configuration["routes"] = [{"match": "tags.cpu", "job_queue": "cpu"}]
    deployment = CloudDeployment.model_validate(configuration)
    with pytest.raises(ValidationError, match="job_definition"):
        deployment.resolve(procedure="example.TokenizeV1", tags=["cpu"])
    settings = deployment.resolve(
        procedure="example.TokenizeV1",
        tags=["cpu"],
        overrides={"job_definition": "worker:12"},
    )
    assert settings.job_queue == "cpu"
    assert settings.timeout is None


def test_partial_settings_survive_snapshot_round_trip(configuration):
    configuration.pop("defaults")
    configuration["routes"] = [
        {"match": "tags.cpu", "job_queue": "cpu"},
        {"match": "tags.cpu", "job_definition": "worker:12"},
    ]
    deployment = CloudDeployment.model_validate(configuration)
    restored = CloudDeployment.model_validate_json(deployment.model_dump_json())
    assert restored.resolve(procedure="example.TokenizeV1", tags=["cpu"]) == (
        deployment.resolve(procedure="example.TokenizeV1", tags=["cpu"])
    )


def test_custom_backend_options_are_preserved_without_resolving_secrets(
    configuration, monkeypatch
):
    monkeypatch.delenv("PROVIUM_DATABASE_URL", raising=False)
    configuration["state"] = {
        "backend": "my-state",
        "options": {"connection_env": "PROVIUM_DATABASE_URL", "custom": [1, True]},
    }
    deployment = CloudDeployment.model_validate(configuration)
    assert deployment.state.model_dump() == configuration["state"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_backend_options_reject_non_json_numbers(configuration, value):
    configuration["state"]["options"]["nested"] = {"values": [value]}
    with pytest.raises(ValidationError):
        CloudDeployment.model_validate(configuration)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "production"),
        ("kind", "PipelineConfiguration"),
        ("provider", "unknown"),
        ("region", " "),
        ("artifacts", {"backend": "s3", "location": "s3://artifacts"}),
        ("state", {"backend": ""}),
        ("defaults", {"compute_environment": "cpu"}),
        ("defaults", {"job_queue": []}),
        ("defaults", {"job_definition": " "}),
        ("defaults", {"job_queue": 1}),
    ],
)
def test_invalid_deployment_fields(configuration, field, value):
    configuration[field] = value
    with pytest.raises(ValidationError):
        CloudDeployment.model_validate(configuration)


@pytest.mark.parametrize(
    "selector",
    ["all", "pipelines.example.nodes.tokenize", "nodes.", "tags.a.b", "procedures."],
)
def test_invalid_selectors(configuration, selector):
    configuration["routes"] = [{"match": selector, "job_queue": "cpu"}]
    with pytest.raises(ValidationError, match="match"):
        CloudDeployment.model_validate(configuration)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(60, 60), ("60s", 60), ("5m", 300), ("2h", 7200), (None, None)],
)
def test_timeout_normalization(value, expected):
    assert SchedulingOptions(timeout=value).timeout == expected


@pytest.mark.parametrize(
    "value", [True, 60.5, 60.0, 0, -1, 59, "59s", "1.5m", "soon", float("inf")]
)
def test_invalid_timeouts(value):
    with pytest.raises(ValidationError, match="timeout"):
        SchedulingOptions(timeout=value)


def test_invalid_overrides_are_not_silently_ignored(configuration):
    deployment = CloudDeployment.model_validate(configuration)
    with pytest.raises(ValidationError, match="job_queu"):
        deployment.resolve(
            procedure="example.TokenizeV1", overrides={"job_queu": "cpu"}
        )
