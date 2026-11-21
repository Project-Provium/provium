"""Artifact transfer retains integrity and immutable references."""

from pathlib import Path

import pytest

from provium_cloud.artifacts import open_artifacts
from provium_cloud.deployment import BackendConfiguration


def test_upload_survives_input_removal_and_fetch_does_not_overwrite(tmp_path, document):
    with open_artifacts(
        BackendConfiguration(
            backend="file", options={"location": str(tmp_path / "store")}
        )
    ) as store:
        reference = store.put(str(document))
        original = document.read_bytes()
        document.unlink()
        destination = tmp_path / "fetched.pa"
        store.fetch(reference, str(destination))
        assert destination.read_bytes() == original
        destination.write_text("unrelated")
        with pytest.raises(FileExistsError):
            store.fetch(reference, str(destination))


def test_corruption_is_detected(tmp_path, document):
    with open_artifacts(
        BackendConfiguration(
            backend="file", options={"location": str(tmp_path / "store")}
        )
    ) as store:
        reference = store.put(str(document))
        from urllib.parse import urlparse

        Path(urlparse(reference["location"]).path).write_text("corrupted")
        with pytest.raises((ValueError, EOFError)):
            store.verify(
                {"source": reference},
                {"source": reference["fingerprint"]["provenance"]},
            )


def test_backend_options_and_unknown_backend_are_validated(tmp_path):
    with pytest.raises(ValueError):
        open_artifacts(BackendConfiguration(backend="unknown"))
    with pytest.raises(ValueError):
        open_artifacts(
            BackendConfiguration(
                backend="file", options={"location": str(tmp_path), "typo": True}
            )
        )


def test_backend_location_mismatch_and_custom_factory(tmp_path, monkeypatch):
    from types import SimpleNamespace

    for backend, location in [("file", "s3://bucket/path"), ("s3", str(tmp_path))]:
        with pytest.raises(ValueError, match="location"):
            open_artifacts(
                BackendConfiguration(backend=backend, options={"location": location})
            )
    sentinel = object()
    monkeypatch.setattr(
        "provium_cloud.artifacts.entry_points",
        lambda **kwargs: [SimpleNamespace(load=lambda: lambda options: sentinel)],
    )
    assert open_artifacts(BackendConfiguration(backend="custom")) is sentinel
