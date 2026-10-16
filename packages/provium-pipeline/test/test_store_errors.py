import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from test_store import make_artifact

from provium_pipeline import ArtifactStore
from provium_pipeline.store import local_path


def test_invalid_store_and_keys(tmp_path):
    with pytest.raises(ValueError):
        local_path("file://otherhost/artifact.pa")
    for url in ("s3://", "s3://bucket?query=x", "s3://bucket#fragment"):
        with pytest.raises(ValueError):
            ArtifactStore(url, scratch=tmp_path)
    with ArtifactStore(str(tmp_path / "store")) as store:
        with pytest.raises(ValueError):
            store.manifest("invalid")
        with pytest.raises(ValueError):
            store.fetch("s3://bucket")
        with pytest.raises(ValueError):
            store.stage("a" * 64, {}, {"output": "b" * 64})
        with pytest.raises(ValueError):
            store.verify({}, {"output": "b" * 64})


def test_remote_download_without_remote_store_and_cleanup(tmp_path, monkeypatch):
    import boto3

    source = make_artifact(tmp_path / "source.pa")
    client = Mock()
    client.download_file.side_effect = lambda bucket, key, path: shutil.copyfile(
        source, path
    )
    monkeypatch.setattr(boto3, "client", lambda service: client)
    with ArtifactStore(str(tmp_path / "store"), scratch=tmp_path) as store:
        artifact = store.inspect("s3://bucket/source.pa")
        assert Path(artifact.path).exists()
        client.download_file.side_effect = OSError("download interrupted")
        with pytest.raises(OSError):
            store.fetch("s3://bucket/failure.pa")
        assert len(list(Path(artifact.path).parent.iterdir())) == 1


def test_content_and_committed_metadata_mismatches(tmp_path, monkeypatch):
    from provium_pipeline import store as module

    source = make_artifact(tmp_path / "source.pa")
    with ArtifactStore(str(tmp_path / "store")) as store:
        artifact = store.inspect(str(source))
        metadata = {
            "out": {
                "location": artifact.location,
                "identity": artifact.identity,
                "fingerprint": artifact.fingerprint,
            }
        }
        for key, value in (("identity", "wrong"), ("fingerprint", {})):
            bad = {"out": {**metadata["out"], key: value}}
            with pytest.raises(ValueError, match="mismatch"):
                store.verify(bad, {"out": artifact.fingerprint["provenance"]})
        with pytest.raises(ValueError, match="mismatch"):
            store.verify(metadata, {"out": "wrong"})
        monkeypatch.setattr(
            module, "artifact_fingerprint", lambda *args: {"content": "wrong"}
        )
        with pytest.raises(ValueError, match="content"):
            store.inspect(str(source))


@pytest.mark.parametrize("recovery", ["missing", "different"])
def test_failed_upload_never_accepts_an_unverified_object(
    tmp_path, monkeypatch, recovery
):
    import boto3

    client = Mock()
    client.upload_file.side_effect = OSError("upload failed")

    def download(bucket, key, path):
        if recovery == "missing":
            raise OSError("missing upload")
        Path(path).write_text("different")

    client.download_file.side_effect = download
    monkeypatch.setattr(boto3, "client", lambda service: client)
    source = tmp_path / "bytes"
    source.write_text("original")
    with ArtifactStore("s3://bucket", scratch=tmp_path) as store:
        with pytest.raises(OSError, match="upload failed"):
            store._put("key", source)


def test_access_denied_is_not_a_cache_miss(tmp_path, monkeypatch):
    import boto3

    client = Mock()
    client.get_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "GetObject"
    )
    monkeypatch.setattr(boto3, "client", lambda service: client)
    with ArtifactStore("s3://bucket", scratch=tmp_path) as store:
        with pytest.raises(ClientError):
            store.manifest("a" * 64)
