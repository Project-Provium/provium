"""Safe deployment loading without AWS or state-backend access."""

import subprocess
import sys

import pytest
import yaml
from pydantic import ValidationError

from provium_cloud.deployment.loader import load_deployment


def test_public_loader_exports():
    import provium_cloud
    from provium_cloud import deployment

    assert (
        provium_cloud.load_deployment is deployment.load_deployment is load_deployment
    )


DEPLOYMENT = """\
kind: CloudDeployment
provider: aws
region: us-east-1
artifacts:
  backend: s3
  options:
    location: s3://my-provium/artifacts
state:
  backend: custom-state
  options:
    connection_env: PROVIUM_DATABASE_URL
defaults:
  job_queue: cpu
  job_definition: worker:12
  timeout: 30m
routes:
  - match: tags.gpu
    job_queue: gpu
"""


@pytest.mark.parametrize("string_path", [False, True])
def test_load_and_resolve_deployment(tmp_path, string_path):
    path = tmp_path / "deployment.yaml"
    path.write_text(DEPLOYMENT, encoding="utf-8")
    deployment = load_deployment(str(path) if string_path else path)
    settings = deployment.resolve(procedure="example.ModelV1", tags=["gpu"])
    assert settings.job_queue == "gpu"
    assert settings.job_definition == "worker:12"
    assert settings.timeout == 1800
    assert deployment.state.backend == "custom-state"


@pytest.mark.parametrize(
    "suffix",
    [
        "region: us-west-2\n",
        "state:\n  backend: postgres\n",
        "extra:\n  job_queue: cpu\n  job_queue: gpu\n",
    ],
)
def test_duplicate_keys_are_rejected(tmp_path, suffix):
    path = tmp_path / "deployment.yaml"
    path.write_text(DEPLOYMENT + suffix, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate YAML key"):
        load_deployment(path)


@pytest.mark.parametrize("text", ["", "null", "[]", "scalar", "{}"])
def test_incomplete_or_non_mapping_documents_are_rejected(tmp_path, text):
    path = tmp_path / "deployment.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_deployment(path)


@pytest.mark.parametrize(
    "text",
    [
        "[broken",
        DEPLOYMENT + "---\n" + DEPLOYMENT,
        "!!python/object:builtins.object {}",
    ],
)
def test_malformed_multiple_or_unsafe_documents_are_rejected(tmp_path, text):
    path = tmp_path / "deployment.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_deployment(path)


@pytest.mark.parametrize("text", ["1: value", "? [a, b]\n: value"])
def test_yaml_keys_must_be_strings(tmp_path, text):
    path = tmp_path / "deployment.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(TypeError, match="keys must be strings"):
        load_deployment(path)


def test_missing_file_preserves_io_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_deployment(tmp_path / "missing.yaml")


def test_import_does_not_load_dispatch_or_aws_sdk():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import provium_cloud, sys; "
            "assert not {'provium_dispatch', 'boto3', 'botocore'} & sys.modules.keys()",
        ],
        check=True,
    )
