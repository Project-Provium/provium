from pathlib import Path

import pytest
import yaml

from infrastructure.aws.__main__ import main


def test_cleanup_requires_a_journal_location():
    with pytest.raises(SystemExit) as error:
        main(["cleanup"])
    assert error.value.code == 2


def test_help_does_not_access_aws():
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0


def test_live_workflow_is_opt_in_and_always_cleans_up():
    root = Path(__file__).resolve().parents[4]
    workflow = yaml.safe_load((root / ".github/workflows/cloud-live.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert "pull_request" not in triggers
    assert "pull_request_target" not in triggers
    assert set(triggers) == {"workflow_dispatch", "push"}
    job = workflow["jobs"]["live"]
    assert "vars.PROVIUM_CLOUD_LIVE_TESTS == 'true'" in job["if"]
    for name in (
        "AWS_ROLE_ARN",
        "AWS_REGION",
        "CLOUDFORMATION_ROLE_ARN",
        "PROVIUM_CLOUD_TEST_ACCOUNT_ID",
    ):
        assert f"vars.{name} != ''" in job["if"]
    assert "github.event.repository.default_branch" in job["if"]
    assert workflow["concurrency"]["cancel-in-progress"] is False
    steps = job["steps"]
    cleanup = next(step for step in steps if step.get("name") == "Recover cleanup")
    assert "always()" in cleanup["if"]
    assert "infrastructure.aws cleanup" in cleanup["run"]
    upload = next(
        step
        for step in steps
        if step.get("uses", "").startswith("actions/upload-artifact")
    )
    assert upload["if"] == "always()"
    assert not upload.get("with", {}).get("include-hidden-files", False)


def test_local_cli_uses_settings_restores_signal_handlers(tmp_path, monkeypatch):
    import signal
    from unittest.mock import MagicMock

    import infrastructure.aws.__main__ as cli

    for name, value in {
        "AWS_REGION": "us-east-1",
        "PROVIUM_CLOUD_TEST_ACCOUNT_ID": "123456789012",
        "CLOUDFORMATION_ROLE_ARN": "arn:aws:iam::123456789012:role/test",
    }.items():
        monkeypatch.setenv(name, value)
    environment = MagicMock()
    factory = MagicMock(return_value=environment)
    monkeypatch.setattr(cli, "Environment", factory)
    docker = MagicMock()
    monkeypatch.setattr(cli.subprocess, "run", docker)
    before = signal.getsignal(signal.SIGTERM)
    main(["run", "--results", str(tmp_path)])
    environment.run.assert_called_once()
    assert signal.getsignal(signal.SIGTERM) == before
    assert factory.call_args.args[0].account_id == "123456789012"
    docker.assert_called_once()
    environment.run.side_effect = RuntimeError("failure")
    with pytest.raises(RuntimeError):
        main(["run", "--results", str(tmp_path)])
    assert signal.getsignal(signal.SIGTERM) == before
    main(["cleanup", "--results", str(tmp_path)])
    factory.resume.assert_called_once_with(tmp_path)
    factory.resume.return_value.cleanup.assert_called_once()


@pytest.fixture(autouse=True)
def isolated_config_root(tmp_path, monkeypatch):
    import os
    from unittest.mock import patch

    import infrastructure.aws.config as config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    with patch.dict(os.environ, {}, clear=True):
        yield


def test_cli_loads_repository_defaults_and_explicit_override(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    import infrastructure.aws.__main__ as cli

    for key in (
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "PROVIUM_CLOUD_TEST_ACCOUNT_ID",
        "CLOUDFORMATION_ROLE_ARN",
        "PROVIUM_CLOUD_TEST_STACK_PREFIX",
    ):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "AWS_REGION=us-east-1\nPROVIUM_CLOUD_TEST_ACCOUNT_ID=123456789012\n"
        "CLOUDFORMATION_ROLE_ARN=arn:aws:iam::123456789012:role/test\n"
    )
    (tmp_path / ".env.local").write_text("PROVIUM_CLOUD_TEST_STACK_PREFIX=local\n")
    factory = MagicMock()
    monkeypatch.setattr(cli, "Environment", factory)
    monkeypatch.setattr(cli.subprocess, "run", MagicMock())
    main(["run", "--results", str(tmp_path / "results")])
    assert factory.call_args.args[0].stack_prefix == "local"
    # Each CLI process starts with the inherited shell, before dotenv loading.
    monkeypatch.delenv("PROVIUM_CLOUD_TEST_STACK_PREFIX")
    explicit = tmp_path / "custom.env"
    explicit.write_text("PROVIUM_CLOUD_TEST_STACK_PREFIX=custom\n")
    main(["run", "--env-file", str(explicit), "--results", str(tmp_path / "results")])
    assert factory.call_args.args[0].stack_prefix == "custom"


def test_make_env_override_is_relative_to_repository():
    import subprocess

    root = Path(__file__).resolve().parents[4]
    for target in ("cloud-live-test", "cloud-live-cleanup"):
        output = subprocess.run(
            [
                "make",
                "-n",
                target,
                "CLOUD_ENV=.env.custom",
                "CLOUD_RESULTS=/tmp/test-run",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert f'--env-file "{root / ".env.custom"}"' in output
    output = subprocess.run(
        ["make", "-n", "cloud-live-test", "CLOUD_ENV="],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "--env-file" not in output


def test_local_env_files_are_ignored_but_examples_are_shareable():
    import subprocess

    root = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        input=".env\n.env.local\n.env.custom\n.env.example\n",
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert set(result.stdout.splitlines()) == {".env", ".env.local", ".env.custom"}
