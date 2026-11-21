from pathlib import Path

import pytest

from infrastructure.aws.config import Settings, load_environment


def test_settings_require_region_account_and_role():
    with pytest.raises(ValueError):
        Settings.from_environment({})
    settings = Settings.from_environment(
        {
            "AWS_REGION": "us-east-1",
            "PROVIUM_CLOUD_TEST_ACCOUNT_ID": "123456789012",
            "CLOUDFORMATION_ROLE_ARN": "arn:aws:iam::123456789012:role/test",
        }
    )
    assert settings.region == "us-east-1"
    assert settings.stack_prefix == "provium-cloud-test"
    assert settings.test_timeout == 1800
    for override in (
        {"PROVIUM_CLOUD_TEST_ACCOUNT_ID": "oops"},
        {"PROVIUM_CLOUD_TEST_TIMEOUT": "0"},
        {"PROVIUM_CLOUD_TEST_STACK_PREFIX": "../unsafe"},
        {"CLOUDFORMATION_ROLE_ARN": "arn:aws:iam::999999999999:role/test"},
    ):
        with pytest.raises(ValueError):
            Settings.from_environment(
                {
                    "AWS_REGION": "us-east-1",
                    "PROVIUM_CLOUD_TEST_ACCOUNT_ID": "123456789012",
                    "CLOUDFORMATION_ROLE_ARN": "arn:aws:iam::123456789012:role/test",
                    **override,
                }
            )


def test_dotenv_does_not_execute_or_expand_values(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "AWS_DEFAULT_REGION=us-west-2\nPROVIUM_CLOUD_TEST_STACK_PREFIX=example\nEXAMPLE_LITERAL=${HOME}\n"
    )
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("EXAMPLE_LITERAL", raising=False)
    load_environment(path)
    import os

    assert os.environ["AWS_DEFAULT_REGION"] == "us-east-1"
    assert os.environ["EXAMPLE_LITERAL"] == "${HOME}"
    with pytest.raises(FileNotFoundError):
        load_environment(Path("/nonexistent/provium-cloud.env"))


@pytest.fixture(autouse=True)
def isolated_dotenv_root(tmp_path, monkeypatch):
    import os
    from unittest.mock import patch

    import infrastructure.aws.config as config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    with patch.dict(os.environ, {}, clear=True):
        yield


def test_environment_layers_preserve_shell_and_literal_values(tmp_path, monkeypatch):
    import os

    (tmp_path / ".env").write_text(
        "BASE=base\nLOCAL=base\nCUSTOM=base\nSHELL=base\nEMPTY=base\nBARE=base\n"
    )
    (tmp_path / ".env.local").write_text(
        "LOCAL=local\nCUSTOM=local\nSHELL=local\nEMPTY=\nBARE\n"
    )
    explicit = tmp_path / "custom.env"
    explicit.write_text("CUSTOM=custom\nSHELL=custom\nLITERAL=${HOME}\n")
    monkeypatch.setenv("SHELL", "shell")
    load_environment(explicit)
    assert {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"} == {
        "BASE": "base",
        "LOCAL": "local",
        "CUSTOM": "custom",
        "SHELL": "shell",
        "EMPTY": "",
        "BARE": "base",
        "LITERAL": "${HOME}",
    }


def test_default_files_are_optional_and_resolved_from_repository(tmp_path, monkeypatch):
    import os

    load_environment()
    assert {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"} == {}
    (tmp_path / ".env").write_text("TEST_ROOT_VALUE=base\n")
    (tmp_path / ".env.local").write_text("TEST_ROOT_VALUE=local\n")
    working = tmp_path / "nested"
    working.mkdir()
    (working / ".env").write_text("TEST_ROOT_VALUE=wrong-directory\n")
    monkeypatch.chdir(working)
    load_environment()
    assert os.environ["TEST_ROOT_VALUE"] == "local"


def test_missing_explicit_file_does_not_partially_load_defaults(tmp_path):
    import os

    (tmp_path / ".env").write_text("DEFAULT_ONLY=value\n")
    with pytest.raises(FileNotFoundError):
        load_environment(tmp_path / "missing.env")
    assert "DEFAULT_ONLY" not in os.environ
