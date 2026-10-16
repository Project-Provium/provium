from pathlib import Path

import pytest
from provium import ImperativeProcedure, session
from provium_text_pipeline_example.artifact import DOCUMENT

from provium_pipeline.store import ArtifactStore


def make_artifact(path: Path, text: str = "hello world") -> Path:
    binding = DOCUMENT.resolve().bind_write(path)
    with session():
        with ImperativeProcedure("fixture.source", "1").execute(
            outputs={"result": binding}
        ):
            with binding.open() as writer:
                writer.write(text)
    return path


def test_publish_validate_and_replace_without_mutating_old_reader(tmp_path):
    store = ArtifactStore(str(tmp_path / "store"), scratch=tmp_path / "scratch")
    first = store.inspect(str(make_artifact(tmp_path / "one.pa")))
    expected = {"result": first.fingerprint["provenance"]}
    outputs = store.stage("a" * 64, {"result": first.path}, expected)
    store.commit("a" * 64, outputs)
    old = store.lookup("a" * 64, expected)["result"]
    second = store.inspect(str(make_artifact(tmp_path / "two.pa", "changed")))
    assert second.fingerprint["provenance"] == first.fingerprint["provenance"]
    replacement = store.stage("a" * 64, {"result": second.path}, expected)
    store.commit("a" * 64, replacement)
    current = store.lookup("a" * 64, expected)["result"]
    assert current.fingerprint["content"] != old.fingerprint["content"]
    assert Path(old.path).exists()


def test_invalid_provenance_and_corruption_rejected(tmp_path):
    store = ArtifactStore(str(tmp_path / "store"))
    path = make_artifact(tmp_path / "one.pa")
    with pytest.raises(ValueError, match="provenance"):
        store.stage("a" * 64, {"result": str(path)}, {"result": "wrong"})
    with path.open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(ValueError):
        store.inspect(str(path))


def test_missing_and_unsupported_store(tmp_path):
    store = ArtifactStore(str(tmp_path))
    assert store.lookup("a" * 64, {}) is None
    with pytest.raises(ValueError):
        ArtifactStore("https://example.com")


def test_direct_writer_lock_rejects_concurrent_context(tmp_path):
    first = ArtifactStore(str(tmp_path))
    second = ArtifactStore(str(tmp_path))
    with first.writer_lock():
        with pytest.raises(RuntimeError, match="writer"):
            with second.writer_lock():
                pass


def test_store_cleans_only_its_own_downloads(tmp_path, monkeypatch):
    import shutil

    source = make_artifact(tmp_path / "source.pa")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    sentinel = scratch / "unrelated"
    sentinel.write_text("keep")

    class Client:
        def download_file(self, bucket, key, path):
            shutil.copyfile(source, path)

    store = ArtifactStore(str(tmp_path / "store"), scratch=scratch)
    store.client = Client()
    downloaded = store.inspect("s3://bucket/input.pa")
    assert Path(downloaded.path).exists()
    store.close()
    assert not Path(downloaded.path).exists()
    assert sentinel.exists()
