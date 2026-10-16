"""Remote file staging and cleanup without requiring AWS credentials."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from provium.files import local_file, normalize_location, publish_file


@pytest.fixture
def s3(monkeypatch):
    import boto3

    objects = {("bucket", "config.yaml"): b"kind: ComputeProfile\nname: local\n"}
    calls = []

    def download(bucket, key, destination):
        calls.append(("download", destination))
        from botocore.exceptions import ClientError

        if (bucket, key) not in objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        Path(destination).write_bytes(objects[bucket, key])

    def upload(source, bucket, key):
        calls.append(("upload", source))
        objects[bucket, key] = Path(source).read_bytes()

    def head(Bucket, Key):
        from botocore.exceptions import ClientError

        if (Bucket, Key) not in objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    client = SimpleNamespace(
        head_object=head,
        download_file=download,
        upload_file=upload,
        delete_object=lambda Bucket, Key: objects.pop((Bucket, Key), None),
    )
    monkeypatch.setattr(boto3, "client", lambda name: client)
    return objects, calls, client


def test_remote_read_uses_scratch_and_cleans_up(tmp_path, s3):
    with local_file("s3://bucket/config.yaml", scratch_directory=tmp_path) as path:
        assert path.parent.parent == tmp_path
        assert path.suffix == ".yaml"
        assert path.read_text().startswith("kind:")
    assert not path.exists()
    assert not list(tmp_path.iterdir())


def test_remote_read_defaults_to_system_temp(s3):
    import tempfile

    with local_file("s3://bucket/config.yaml") as path:
        assert path.parent.parent == Path(tempfile.gettempdir())


def test_local_paths_and_file_urls_are_not_copied(tmp_path):
    source = tmp_path / "space name.txt"
    source.write_text("source")
    for location in (source, str(source), source.as_uri()):
        with local_file(location) as path:
            assert path == source
    assert source.read_text() == "source"
    assert normalize_location("relative.txt") == Path("relative.txt")


@pytest.mark.parametrize(
    "location",
    [
        "https://example.com/file",
        "s3://bucket",
        "s3:///key",
        "s3://bucket/key?query",
        "s3://bucket/key#fragment",
        "file://remote-host/file",
    ],
)
def test_invalid_locations(location):
    with pytest.raises(ValueError):
        normalize_location(location)


def test_failed_download_cleans_owned_scratch(tmp_path, s3):
    with pytest.raises(FileNotFoundError):
        with local_file("s3://bucket/missing", scratch_directory=tmp_path):
            pytest.fail("missing download cannot yield a path")
    assert not list(tmp_path.iterdir())


def test_publish_uploads_finished_file_only(tmp_path, s3):
    source = tmp_path / "ready.pa"
    source.write_bytes(b"finished")
    publish_file(source, "s3://bucket/output.pa")
    assert s3[0]["bucket", "output.pa"] == b"finished"
    assert not source.exists()


def test_failed_upload_preserves_source(tmp_path, s3, monkeypatch):
    source = tmp_path / "ready.pa"
    source.write_bytes(b"finished")

    def fail(*args):
        raise OSError("denied")

    monkeypatch.setattr(s3[2], "upload_file", fail)
    with pytest.raises(OSError, match="denied"):
        publish_file(source, "s3://bucket/output.pa")
    assert source.exists()


def test_publish_local_replace(tmp_path):
    source, destination = tmp_path / "ready", tmp_path / "output"
    source.write_text("new")
    destination.write_text("old")
    publish_file(source, destination)
    assert destination.read_text() == "new"
    assert not source.exists()


def test_missing_s3_extra_has_actionable_error(monkeypatch):
    import builtins

    original = builtins.__import__

    def without_boto(name, *args, **kwargs):
        if name == "boto3":
            raise ImportError("not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_boto)
    with pytest.raises(RuntimeError, match=r"provium\[s3\]"):
        with local_file("s3://bucket/file"):
            pytest.fail("missing S3 extra")


def test_bindings_preserve_s3_locations(monkeypatch):
    from provium.artifact import binding

    monkeypatch.setattr(binding, "validate_artifact_class", lambda artifact: artifact)
    for cls in (binding.ArtifactReadBinding, binding.ArtifactWriteBinding):
        assert cls(object, "s3://bucket/input.pa").path == "s3://bucket/input.pa"


def test_session_scratch_is_inherited(tmp_path):
    from provium import session

    with session(scratch_directory=tmp_path) as outer:
        with session() as inner:
            assert inner.scratch_directory == outer.scratch_directory == tmp_path


def test_core_configuration_loaders_accept_s3(tmp_path, s3):
    from provium import load_json_configuration, load_yaml_configuration, session

    s3[0]["bucket", "config.json"] = b'{"value": 42}'
    s3[0]["bucket", "settings.yaml"] = b"value: 42"
    with session(scratch_directory=tmp_path):
        assert load_json_configuration("s3://bucket/config.json") == {"value": 42}
        assert load_yaml_configuration("s3://bucket/settings.yaml") == {"value": 42}
    assert not list(tmp_path.iterdir())


def test_remote_access_errors_are_not_missing(tmp_path, s3, monkeypatch):
    from botocore.exceptions import ClientError

    from provium.files import file_exists

    def denied(*args, **kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    monkeypatch.setattr(s3[2], "download_file", denied)
    monkeypatch.setattr(s3[2], "head_object", denied)
    with pytest.raises(ClientError):
        with local_file("s3://bucket/file", scratch_directory=tmp_path):
            pytest.fail("denied")
    with pytest.raises(ClientError):
        file_exists("s3://bucket/file")
    assert not list(tmp_path.iterdir())


def test_file_existence_and_removal(tmp_path, s3):
    from provium.files import file_exists, remove_file

    path = tmp_path / "file"
    assert not file_exists(path)
    path.touch()
    assert file_exists(path)
    remove_file(path)
    assert not file_exists(path)
    assert file_exists("s3://bucket/config.yaml")
    remove_file("s3://bucket/config.yaml")
    assert not file_exists("s3://bucket/config.yaml")


def test_writable_file_override_and_abort(tmp_path, s3):
    from provium.files import writable_file

    with writable_file("s3://bucket/output.txt", scratch_directory=tmp_path) as path:
        path.write_text("result")
        assert path.parent.parent == tmp_path
    assert s3[0]["bucket", "output.txt"] == b"result"
    with pytest.raises(RuntimeError):
        with writable_file(
            "s3://bucket/output.txt", scratch_directory=tmp_path
        ) as path:
            path.write_text("broken")
            raise RuntimeError()
    assert s3[0]["bucket", "output.txt"] == b"result"
    assert not list(tmp_path.iterdir())


def test_scratch_override_is_created_and_keys_cannot_escape_staging(tmp_path, s3):
    from provium.files import writable_file

    scratch = tmp_path / "new" / "scratch"
    s3[0]["bucket", ".."] = b"object with an unusual key"
    with local_file("s3://bucket/..", scratch_directory=scratch) as path:
        assert path.parent.parent == scratch
        assert path.read_bytes() == b"object with an unusual key"
    with writable_file("s3://bucket/other", scratch_directory=scratch) as path:
        path.write_bytes(b"new")
    assert not list(scratch.iterdir())


def test_writable_file_defaults_to_system_temp(s3):
    import tempfile

    from provium.files import writable_file

    with writable_file("s3://bucket/output.txt") as path:
        assert path.parent.parent == Path(tempfile.gettempdir())
        path.write_text("result")
    assert not path.exists()
