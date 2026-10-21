from pathlib import Path
from types import SimpleNamespace

import pytest
from provium import cli

from provium_pipeline.models import load_documents


@pytest.fixture
def remote(monkeypatch):
    import boto3

    files = {
        "pipeline.yaml": (
            b"kind: PipelineConfiguration\nname: remote\n"
            b"inputs: {}\nnodes: {}\noutputs: {}\n"
        ),
        "profiles.yaml": (
            b"kind: ComputeProfile\nname: local\n---\n"
            b"kind: ComputeProfile\nname: cluster\n"
        ),
        "invalid.yaml": b"kind: ComputeProfile\nname: x\nname: y\n",
    }
    paths = []

    def download(bucket, key, target):
        assert bucket == "configs"
        paths.append(Path(target))
        Path(target).write_bytes(files[key])

    monkeypatch.setattr(
        boto3, "client", lambda name: SimpleNamespace(download_file=download)
    )
    return paths


def test_remote_documents_mix_with_local_and_cleanup(tmp_path, remote):
    local = tmp_path / "local.yaml"
    local.write_text("kind: ComputeProfile\nname: extra\n")
    docs = load_documents(
        ["s3://configs/pipeline.yaml", local, "s3://configs/profiles.yaml"],
        scratch_directory=tmp_path,
    )
    assert [doc.name for doc in docs] == ["remote", "extra", "local", "cluster"]
    assert all(path.parent.parent == tmp_path and not path.exists() for path in remote)
    assert list(tmp_path.iterdir()) == [local]


def test_invalid_remote_yaml_cleans_download(tmp_path, remote):
    with pytest.raises(ValueError, match="duplicate"):
        load_documents(["s3://configs/invalid.yaml"], scratch_directory=tmp_path)
    assert not list(tmp_path.iterdir())


def test_cli_remote_file_uses_scratch(tmp_path, remote, capsys):
    assert (
        cli.run(
            [
                "pipeline",
                "validate",
                "-f",
                "s3://configs/pipeline.yaml",
                "-f",
                "s3://configs/profiles.yaml",
                "--profile",
                "local",
                "--scratch-directory",
                str(tmp_path),
                "--store",
                str(tmp_path / "store"),
            ]
        )
        == 0
    )
    assert '"pipeline": "remote"' in capsys.readouterr().out
    assert all(path.parent.parent == tmp_path and not path.exists() for path in remote)


def test_localstack_core_artifacts_and_remote_pipeline_yaml(tmp_path, capsys):
    import argparse
    import json
    import os
    from uuid import uuid4

    if not os.environ.get("PROVIUM_TEST_S3"):
        pytest.skip("requires LocalStack S3")
    import boto3
    from provium import session
    from provium.cli.commands.artifact import ArtifactCommand
    from provium.cli.commands.execute import ExecuteCommand
    from provium_text_pipeline_example.artifact import DOCUMENT, TOKENS
    from test_runtime import configuration

    client = boto3.client("s3")
    bucket = "provium-paths-" + uuid4().hex
    client.create_bucket(Bucket=bucket)
    try:
        prefix = f"s3://{bucket}"
        client.put_object(Bucket=bucket, Key="source.txt", Body=b"hello remote world")
        graph = configuration()
        import yaml

        client.put_object(
            Bucket=bucket,
            Key="pipeline.yaml",
            Body=yaml.safe_dump(graph.model_dump(mode="json")).encode(),
        )
        command = ArtifactCommand()
        parser = argparse.ArgumentParser()
        command.configure(parser)
        assert (
            command.execute(
                parser.parse_args(
                    [
                        "load",
                        DOCUMENT.identifier,
                        prefix + "/source.txt",
                        prefix + "/source.pa",
                        "--scratch-directory",
                        str(tmp_path),
                    ]
                )
            )
            == 0
        )

        # Direct core execution also accepts remote bindings and preserves result URIs.
        direct = ExecuteCommand()
        parser = argparse.ArgumentParser(add_help=False)
        direct.configure(parser)
        assert (
            direct.execute(
                parser.parse_args(
                    [
                        "provium_text_pipeline_example.TokenizeV1",
                        "--input",
                        "source=" + prefix + "/source.pa",
                        "--output",
                        "destination=" + prefix + "/tokens.pa",
                        "--scratch-directory",
                        str(tmp_path),
                    ]
                )
            )
            == 0
        )
        capsys.readouterr()
        assert (
            cli.run(
                [
                    "pipeline",
                    "run",
                    "-f",
                    prefix + "/pipeline.yaml",
                    "--input",
                    "document=" + prefix + "/source.pa",
                    "--store",
                    prefix + "/store",
                    "--scratch-directory",
                    str(tmp_path),
                ]
            )
            == 0
        )
        outcomes = json.loads(capsys.readouterr().out)
        assert outcomes[0]["state"] == "completed"
        with session(scratch_directory=tmp_path):
            with TOKENS.resolve().open(prefix + "/tokens.pa") as reader:
                assert reader.body.read()
    finally:
        objects = client.list_objects_v2(Bucket=bucket).get("Contents", [])
        if objects:
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
            )
        client.delete_bucket(Bucket=bucket)
