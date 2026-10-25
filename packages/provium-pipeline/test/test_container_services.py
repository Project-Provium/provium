import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from container_services import start_services


@pytest.mark.parametrize("postgres", [False, True])
def test_services_start_once_and_restore_environment(monkeypatch, postgres):
    import container_services

    localstack = MagicMock()
    localstack.return_value.__enter__.return_value.get_url.return_value = "http://s3"
    database = MagicMock()
    database.return_value.__enter__.return_value.get_connection_url.return_value = (
        "postgresql://database"
    )
    monkeypatch.setattr(container_services, "LocalStackContainer", localstack)
    monkeypatch.setattr(container_services, "PostgresContainer", database)
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "original")
    monkeypatch.setenv("PROVIUM_TEST_DATABASE", "original-database")
    cleanups = []
    config = SimpleNamespace(stash={}, add_cleanup=cleanups.append)
    start_services(config, postgres=postgres)
    start_services(config, postgres=postgres)
    assert localstack.call_count == 1
    assert database.call_count == int(postgres)
    assert os.environ["AWS_ENDPOINT_URL_S3"] == "http://s3"
    assert os.environ["PROVIUM_TEST_S3"] == "1"
    if postgres:
        assert os.environ["PROVIUM_TEST_DATABASE"] == "postgresql://database"
    for cleanup in reversed(cleanups):
        cleanup()
    assert os.environ["AWS_ENDPOINT_URL_S3"] == "original"
    assert os.environ["PROVIUM_TEST_DATABASE"] == "original-database"
    assert localstack.return_value.__exit__.call_count == 1
    assert database.return_value.__exit__.call_count == int(postgres)


def test_start_failure_still_cleans_up(monkeypatch):
    import container_services

    localstack = MagicMock()
    localstack.return_value.__enter__.return_value.get_url.return_value = "http://s3"
    database = MagicMock()
    database.return_value.__enter__.side_effect = RuntimeError("startup failed")
    monkeypatch.setattr(container_services, "LocalStackContainer", localstack)
    monkeypatch.setattr(container_services, "PostgresContainer", database)
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "original")
    cleanups = []
    config = SimpleNamespace(stash={}, add_cleanup=cleanups.append)
    with pytest.raises(RuntimeError, match="startup failed"):
        start_services(config, postgres=True)
    for cleanup in reversed(cleanups):
        cleanup()
    assert localstack.return_value.__exit__.call_count == 1
    assert os.environ["AWS_ENDPOINT_URL_S3"] == "original"
