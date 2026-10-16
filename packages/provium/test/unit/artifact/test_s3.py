import pytest
from test_files import s3 as s3

from provium import (
    Artifact,
    ArtifactDefinition,
    ArtifactReader,
    ArtifactWriter,
    ProcedureRecord,
    session,
)
from provium.procedure.execution import ProcedureExecutionSession


class Reader(ArtifactReader):
    pass


class Writer(ArtifactWriter):
    def write(self, value: bytes) -> int:
        return self.body.write(value)


class BytesArtifact(Artifact[Reader, Writer]):
    definition = ArtifactDefinition(
        identifier="example.RemoteBytesV1",
        target=f"{__name__}:BytesArtifact",
        description="Remote bytes",
    )
    reader = Reader
    writer = Writer


def test_s3_artifact_roundtrip_and_cleanup(tmp_path, s3):
    location = "s3://bucket/artifact.pa"
    with session(scratch_directory=tmp_path):
        with ProcedureExecutionSession(
            ProcedureRecord("example.CreateV1", "digest")
        ) as execution:
            writer = execution.stage_outputs(
                {"result": BytesArtifact.bind_write(location)}
            )["result"]
            writer.body.write(b"remote body")
        from provium.artifact.header import read_artifact_header
        from provium.artifact.inspection import inspect_finalized_artifact

        assert read_artifact_header(location).body_length == 11
        inspection = inspect_finalized_artifact(location, scratch_directory=tmp_path)
        assert inspection.path == location
        with BytesArtifact.open(location) as reader:
            assert reader.body.read() == b"remote body"
    assert not list(tmp_path.iterdir())


def test_s3_artifact_failure_does_not_replace_existing_object(tmp_path, s3):
    location = "s3://bucket/artifact.pa"
    s3[0]["bucket", "artifact.pa"] = b"original"
    with pytest.raises(RuntimeError, match="procedure failed"):
        with session(scratch_directory=tmp_path):
            with ProcedureExecutionSession(
                ProcedureRecord("example.CreateV1", "digest")
            ) as execution:
                writer = execution.stage_outputs(
                    {"result": BytesArtifact.bind_write(location)}
                )["result"]
                writer.body.write(b"not committed")
                raise RuntimeError("procedure failed")
    assert s3[0]["bucket", "artifact.pa"] == b"original"
    assert not list(tmp_path.iterdir())


def test_artifact_cli_load_and_dump_s3(tmp_path, s3, monkeypatch):
    import argparse

    from provium.cli.commands import artifact as module

    monkeypatch.setattr(module, "_artifact", lambda identifier: BytesArtifact)
    monkeypatch.setattr(
        BytesArtifact,
        "load",
        staticmethod(lambda writer, path: writer.body.write(path.read_bytes())),
    )
    monkeypatch.setattr(
        BytesArtifact,
        "dump",
        staticmethod(lambda reader, path: path.write_bytes(reader.body.read())),
    )
    s3[0]["bucket", "input.txt"] = b"CLI body"
    command = module.ArtifactCommand()
    parser = argparse.ArgumentParser()
    command.configure(parser)
    for arguments in [
        [
            "load",
            BytesArtifact.definition.identifier,
            "s3://bucket/input.txt",
            "s3://bucket/result.pa",
        ],
        ["dump", "s3://bucket/result.pa", "s3://bucket/output.txt"],
    ]:
        assert (
            command.execute(
                parser.parse_args([*arguments, "--scratch-directory", str(tmp_path)])
            )
            == 0
        )
    assert s3[0]["bucket", "output.txt"] == b"CLI body"
    assert not list(tmp_path.iterdir())


def test_multi_output_remote_upload_failure_restores_old_objects(
    tmp_path, s3, monkeypatch
):
    s3[0]["bucket", "old.pa"] = b"original"
    upload = s3[2].upload_file
    failed = False

    def fail_once_after_upload(source, bucket, key):
        nonlocal failed
        upload(source, bucket, key)
        if key == "old.pa" and not failed:
            failed = True
            raise OSError("lost upload acknowledgement")

    monkeypatch.setattr(s3[2], "upload_file", fail_once_after_upload)
    with pytest.raises(OSError, match="acknowledgement"):
        with session(scratch_directory=tmp_path):
            with ProcedureExecutionSession(
                ProcedureRecord("example.CreateV1", "digest")
            ) as execution:
                writers = execution.stage_outputs(
                    {
                        name: BytesArtifact.bind_write(f"s3://bucket/{name}.pa")
                        for name in ("new", "old", "untouched")
                    }
                )
                for writer in writers.values():
                    writer.body.write(b"replacement")
    assert s3[0]["bucket", "old.pa"] == b"original"
    assert ("bucket", "new.pa") not in s3[0]
    assert ("bucket", "untouched.pa") not in s3[0]
    assert not list(tmp_path.iterdir())


def test_remote_result_retains_uri():
    from provium.procedure.result import ProcedureOutputResult

    assert (
        ProcedureOutputResult("s3://bucket/result.pa").path == "s3://bucket/result.pa"
    )


def test_graph_cli_uses_remote_files(tmp_path, s3, monkeypatch):
    import argparse

    from provium.cli.commands import graph as module

    with session():
        with ProcedureExecutionSession(
            ProcedureRecord("example.CreateV1", "digest")
        ) as execution:
            execution.stage_outputs(
                {"result": BytesArtifact.bind_write("s3://bucket/input.pa")}
            )
    command = module.GraphCommand()
    parser = argparse.ArgumentParser()
    command.configure(parser)
    assert (
        command.execute(
            parser.parse_args(
                [
                    "source",
                    "s3://bucket/input.pa",
                    "dot",
                    "s3://bucket/graph.dot",
                    "--scratch-directory",
                    str(tmp_path),
                ]
            )
        )
        == 0
    )
    assert b"digraph" in s3[0]["bucket", "graph.dot"]
    monkeypatch.setattr(module, "render_lineage", lambda *a, **kw: b"image")
    assert (
        command.execute(
            parser.parse_args(
                [
                    "render",
                    "s3://bucket/input.pa",
                    "s3://bucket/image",
                    "--scratch-directory",
                    str(tmp_path),
                ]
            )
        )
        == 0
    )
    assert s3[0]["bucket", "image.png"] == b"image"
    assert not list(tmp_path.iterdir())
