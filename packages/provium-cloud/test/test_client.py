from unittest.mock import Mock

import pytest

from provium_cloud import CloudClient
from provium_cloud.deployment import CloudDeployment


def deployment(tmp_path):
    return CloudDeployment.model_validate(
        {
            "provider": "aws",
            "region": "us-east-1",
            "state": {
                "backend": "sqlite",
                "options": {"path": str(tmp_path / "state")},
            },
            "artifacts": {
                "backend": "file",
                "options": {"location": str(tmp_path / "artifacts")},
            },
            "defaults": {"job_queue": "cpu", "job_definition": "worker:1"},
        }
    )


def test_client_owned_resources_close_on_open_failure(tmp_path, monkeypatch):
    artifact = Mock()
    monkeypatch.setattr("provium_cloud.client.open_artifacts", lambda _: artifact)

    def fail(*args, **kwargs):
        raise RuntimeError("state unavailable")

    monkeypatch.setattr("provium_cloud.client.open_state", fail)
    with pytest.raises(RuntimeError):
        CloudClient(deployment(tmp_path))
    artifact.close.assert_called_once()
    artifact.close.reset_mock()
    with pytest.raises(RuntimeError):
        CloudClient(deployment(tmp_path), artifacts=artifact)
    artifact.close.assert_not_called()


def test_injected_backend_is_not_closed(tmp_path):
    artifact = Mock()
    with CloudClient(deployment(tmp_path), artifacts=artifact, state=Mock()):
        pass
    artifact.close.assert_not_called()


def test_client_queries_wait_and_missing_outputs(tmp_path, document, monkeypatch):
    import yaml

    path = tmp_path / "deployment.yaml"
    path.write_text(yaml.safe_dump(deployment(tmp_path).model_dump(mode="json")))
    with CloudClient.from_file(path) as client:
        run = client.enqueue_procedure(
            "provium_text_pipeline_example.TokenizeV1",
            {"source": str(document)},
            request_id="one",
        )
        assert (
            client.enqueue_procedure(
                "provium_text_pipeline_example.TokenizeV1",
                {"source": str(document)},
                request_id="one",
            ).id
            == run.id
        )
        assert client.lookup("missing") is None
        assert client.attempts(run.id, "procedure") is None
        for options in ({"interval": 0}, {"timeout": -1}):
            with pytest.raises(ValueError):
                client.wait(run.id, **options)
        with pytest.raises(LookupError):
            client.fetch(run.id, "destination", str(tmp_path / "not-yet.pa"))
        monkeypatch.setattr(
            "provium_cloud.client.sleep", lambda _: client.cancel(run.id)
        )
        assert client.wait(run.id).status == "canceled"
