import json

import pytest
import yaml
from provium.cli import run


@pytest.fixture
def deployment_file(tmp_path):
    path = tmp_path / "deployment.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "provider": "aws",
                "region": "us-east-1",
                "state": {
                    "backend": "sqlite",
                    "options": {"path": str(tmp_path / "state.db")},
                },
                "artifacts": {
                    "backend": "file",
                    "options": {"location": str(tmp_path / "artifacts")},
                },
                "defaults": {"job_queue": "cpu", "job_definition": "worker:1"},
            }
        )
    )
    return path


def test_cli_enqueue_inspect_cancel_retry_and_list(deployment_file, document, capsys):
    capsys.readouterr()
    args = ["cloud", "aws"]
    dep = ["-d", str(deployment_file)]
    assert (
        run(
            [
                *args,
                "enqueue",
                *dep,
                "--procedure",
                "provium_text_pipeline_example.TokenizeV1",
                "--input",
                f"source={document}",
                "--job-queue",
                "urgent",
            ]
        )
        == 0
    )
    record = json.loads(capsys.readouterr().out)
    identity = record["id"]
    assert record["progress"]["total"] == 1
    assert record["tasks"]["procedure"]["plan"]["settings"]["job_queue"] == "urgent"
    for action in ["status", "inspect", "cancel", "retry"]:
        assert run([*args, action, *dep, identity]) == 0
        assert json.loads(capsys.readouterr().out)["id"] == identity
    assert run([*args, "list", *dep, "--status", "queued"]) == 0
    assert len(json.loads(capsys.readouterr().out)["items"]) == 1
    assert run([*args, "wait", *dep, identity, "--timeout", "0"]) == 2
    assert "timed out" in capsys.readouterr().err


def test_plan_never_opens_backends(deployment_file, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("planning must not open backends")

    monkeypatch.setattr("provium_cloud.client.open_state", forbidden)
    monkeypatch.setattr("provium_cloud.client.open_artifacts", forbidden)
    assert (
        run(
            [
                "cloud",
                "aws",
                "plan",
                "-d",
                str(deployment_file),
                "--procedure",
                "provium_text_pipeline_example.TokenizeV1",
            ]
        )
        == 0
    )
    assert "procedure" in json.loads(capsys.readouterr().out)["tasks"]


@pytest.mark.parametrize(
    "action",
    [
        "enqueue",
        "plan",
        "list",
        "status",
        "inspect",
        "cancel",
        "retry",
        "wait",
        "lookup",
        "fetch",
        "logs",
        "controller",
        "worker",
    ],
)
def test_management_help(action):
    with pytest.raises(SystemExit) as result:
        run(["cloud", "aws", action, "--help"])
    assert result.value.code == 0


def test_cli_pipeline_and_deployment_overrides(
    deployment_file, document, capsys, tmp_path
):
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "inputs": {"source": "provium_text_pipeline_example.DocumentV1"},
                "nodes": {
                    "tokens": {
                        "procedure": "provium_text_pipeline_example.TokenizeV1",
                        "inputs": {"source": "$inputs.source"},
                    }
                },
                "targets": {"tokens": "$nodes.tokens.destination"},
            }
        )
    )
    capsys.readouterr()
    args = [
        "cloud",
        "aws",
        "plan",
        "-d",
        str(deployment_file),
        "-f",
        str(path),
        "--region",
        "us-west-2",
        "--deployment-set",
        "defaults.job_queue=other",
    ]
    assert run(args) == 0
    assert (
        json.loads(capsys.readouterr().out)["tasks"]["tokens"]["settings"]["job_queue"]
        == "other"
    )
    for extra in (["--parameter", "n=1"], ["--tag", "tag"]):
        assert run([*args, *extra]) == 2
        capsys.readouterr()
    args = [
        "cloud",
        "aws",
        "enqueue",
        "-d",
        str(deployment_file),
        "--procedure",
        "provium_text_pipeline_example.TokenizeV1",
    ]
    for extra in (
        ["--target", "tokens"],
        ["--force-selector", "nodes.x"],
        ["--input", "bad"],
        ["--input", "a=x", "--input", "a=y"],
    ):
        assert run([*args, *extra]) == 2
        capsys.readouterr()


def test_cli_execution_artifact_and_wait_actions(
    deployment_file, document, capsys, monkeypatch, tmp_path
):
    # Native CLI routing is exercised; only the external compute service is replaced.
    from cloud_fixtures import LocalBatch, finish

    from provium_cloud import CloudClient
    from provium_cloud.execution.controller import Controller

    with CloudClient.from_file(deployment_file) as client:
        executor = LocalBatch()
        record = client.enqueue_procedure(
            "provium_text_pipeline_example.TokenizeV1", {"source": str(document)}
        )
        result = finish(client, Controller(client, executor=executor), executor, record)
    capsys.readouterr()
    prefix = ["cloud", "aws"]
    dep = ["-d", str(deployment_file)]
    assert run([*prefix, "wait", *dep, result.id]) == 0
    capsys.readouterr()
    assert run([*prefix, "inspect", *dep, result.id, "--node", "procedure"]) == 0
    computation = json.loads(capsys.readouterr().out)["key"]
    assert run([*prefix, "lookup", *dep, computation]) == 0
    assert json.loads(capsys.readouterr().out)["outputs"]
    assert (
        run(
            [
                *prefix,
                "fetch",
                *dep,
                result.id,
                "destination",
                str(tmp_path / "fetch.pa"),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert run([*prefix, "controller", *dep, "--once"]) == 0
    monkeypatch.setattr(Controller, "serve", lambda *args, **kwargs: None)
    assert run([*prefix, "controller", *dep]) == 0
    assert not capsys.readouterr().out
    monkeypatch.setenv("PROVIUM_CLOUD_DEPLOYMENT", client.deployment.model_dump_json())
    monkeypatch.setattr(
        "provium_cloud.execution.worker.execute_job",
        lambda *args, **kwargs: False,
    )
    assert (
        run(
            [
                *prefix,
                "worker",
                "--computation",
                computation,
                "--generation",
                "1",
                "--attempt",
                "x",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"executed": False}

    class Logs:
        def __init__(self, region):
            pass

        def logs(self, *args, **kwargs):
            return {"events": [], "next_cursor": None}

    monkeypatch.setattr("provium_cloud.cli.aws.commands.execution.BatchExecutor", Logs)
    assert run([*prefix, "logs", *dep, "job"]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == []


def test_batch_worker_command_executes_in_a_fresh_process(deployment_file, document):
    import os
    import subprocess
    import sys

    from cloud_fixtures import LocalBatch

    from provium_cloud import CloudClient
    from provium_cloud.execution.controller import Controller

    with CloudClient.from_file(deployment_file) as client:
        executor = LocalBatch()
        record = client.enqueue_procedure(
            "provium_text_pipeline_example.TokenizeV1", {"source": str(document)}
        )
        Controller(client, executor=executor).tick()
        key, generation, _, _ = executor.jobs["job-1"]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "provium",
                "cloud",
                "aws",
                "worker",
                "--computation",
                key,
                "--generation",
                "1",
                "--attempt",
                generation.attempt,
            ],
            env={
                **os.environ,
                "PROVIUM_CLOUD_DEPLOYMENT": client.deployment.model_dump_json(),
                "AWS_BATCH_JOB_ID": "job-1",
            },
            capture_output=True,
            text=True,
            check=True,
        )
        assert json.loads(result.stdout) == {"executed": True}
        assert client.get_run(record.id).status == "succeeded"
