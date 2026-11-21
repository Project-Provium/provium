import json
from unittest.mock import MagicMock

from psycopg.conninfo import conninfo_to_dict

from infrastructure.aws.runtime import bootstrap, driver


def test_bootstrap_fetches_secret_without_passing_it_in_arguments(monkeypatch):
    secret = MagicMock()
    secret.get_secret_value.return_value = {
        "SecretString": json.dumps(
            {"username": "cloudtest", "password": "secret ' spaced"}
        )
    }
    monkeypatch.setenv("DB_SECRET_ARN", "secret-arn")
    monkeypatch.setenv("DB_HOST", "private.example")
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *a, **kw: secret)
    execute = MagicMock()
    monkeypatch.setattr(bootstrap.os, "execvpe", execute)
    bootstrap.main(["provium", "--help"])
    program, args, env = execute.call_args.args
    assert program == "provium"
    assert args == ["provium", "--help"]
    info = conninfo_to_dict(env["PROVIUM_CLOUD_DATABASE_URL"])
    assert info["password"] == "secret ' spaced"
    assert info["sslmode"] == "require"
    assert info["host"] == "private.example"
    assert info["connect_timeout"] == "10"


def test_driver_uploads_report_and_preserves_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_BUCKET", "bucket")
    monkeypatch.setattr(driver, "REPORTS", tmp_path)
    s3 = MagicMock()
    monkeypatch.setattr(driver.boto3, "client", lambda *a, **kw: s3)

    def pytest_main(args):
        (tmp_path / "junit.xml").write_text("<testsuites/>")
        return 1

    monkeypatch.setattr(driver.pytest, "main", pytest_main)
    assert driver.main() == 1
    assert s3.upload_file.call_count == 2
    assert json.loads((tmp_path / "summary.json").read_text())["exit_code"] == 1


def test_live_suite_contract_locally(tmp_path, monkeypatch):
    import os
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    monkeypatch.setenv(
        "PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    )

    from cloud_fixtures import LocalBatch

    from infrastructure.aws.runtime import live_test
    from provium_cloud import CloudClient
    from provium_cloud.deployment import CloudDeployment
    from provium_cloud.execution.controller import Controller

    for name in ("AWS_DEFAULT_REGION", "TEST_BUCKET", "TEST_QUEUE", "TEST_WORKER"):
        monkeypatch.setenv(name, "local-test")

    def local_deployment(value):
        return CloudDeployment.model_validate(
            {
                **value,
                "state": {
                    "backend": "sqlite",
                    "options": {"path": str(tmp_path / "state.db")},
                },
                "artifacts": {
                    "backend": "file",
                    "options": {"location": str(tmp_path / "artifacts")},
                },
            }
        )

    executor = LocalBatch()

    class LocalController(Controller):
        def tick(self):
            super().tick()
            executor.drain()

    monkeypatch.setattr(
        live_test, "CloudDeployment", SimpleNamespace(model_validate=local_deployment)
    )
    monkeypatch.setattr(
        live_test,
        "CloudClient",
        lambda deployment: CloudClient(deployment, executor=executor),
    )
    monkeypatch.setattr(live_test, "Controller", LocalController)
    monkeypatch.setattr(live_test.time, "sleep", lambda _: None)
    live_test.test_pipeline_procedure_deduplication_force_and_management(tmp_path)
