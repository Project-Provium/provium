"""The same durable-state contract applies to local and PostgreSQL storage."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from provium_cloud.deployment import BackendConfiguration
from provium_cloud.state import open_state
from provium_cloud.state.models import Computation


@pytest.fixture(
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.integration)]
)
def backend(request, tmp_path):
    if request.param == "sqlite":
        config = BackendConfiguration(
            backend="sqlite", options={"path": str(tmp_path / "state.sqlite")}
        )
        yield open_state(config, namespace="artifacts")
    else:
        from testcontainers.community.postgres import PostgresContainer

        with PostgresContainer("postgres:17-alpine", driver=None) as postgres:
            import os
            from unittest.mock import patch

            with patch.dict(
                os.environ, {"CLOUD_TEST_DATABASE": postgres.get_connection_url()}
            ):
                config = BackendConfiguration(
                    backend="postgres",
                    options={"connection_env": "CLOUD_TEST_DATABASE"},
                )
                yield open_state(config, namespace="artifacts")


def test_transaction_rollback_and_isolated_reads(backend):
    with backend.transaction() as tx:
        tx.save_computation(Computation(key="a"))
    with pytest.raises(RuntimeError), backend.transaction() as tx:
        tx.save_computation(Computation(key="b"))
        raise RuntimeError("rollback")
    with backend.transaction() as tx:
        assert tx.get_computation("a").key == "a"
        assert tx.get_computation("b") is None
        assert tx.now > 0


def test_concurrent_transactions_do_not_lose_updates(backend):
    def update(_):
        with backend.transaction() as tx:
            old = tx.request("counter")
            count = 0 if old is None else int(old[0])
            tx.save_request("counter", str(count + 1), "hash")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(update, range(12)))
    with backend.transaction() as tx:
        assert tx.request("counter") == ("12", "hash")


def test_unknown_state_backend_and_options_fail(tmp_path):
    with pytest.raises(ValueError):
        open_state(BackendConfiguration(backend="missing"), namespace="test")
    with pytest.raises(ValueError):
        open_state(
            BackendConfiguration(
                backend="sqlite", options={"path": str(tmp_path / "state"), "typo": 1}
            ),
            namespace="test",
        )


def test_custom_state_factory_and_missing_connection(tmp_path, monkeypatch):
    from types import SimpleNamespace

    sentinel = object()
    monkeypatch.setattr(
        "provium_cloud.state.entry_points",
        lambda **kwargs: [
            SimpleNamespace(load=lambda: lambda options, namespace: sentinel)
        ],
    )
    assert (
        open_state(BackendConfiguration(backend="custom"), namespace="test") is sentinel
    )
    monkeypatch.delenv("MISSING_CLOUD_CONNECTION", raising=False)
    with pytest.raises(ValueError, match="connection"):
        open_state(
            BackendConfiguration(
                backend="postgres",
                options={"connection_env": "MISSING_CLOUD_CONNECTION"},
            ),
            namespace="test",
        )
    with pytest.raises(ValueError):
        open_state(
            BackendConfiguration(backend="sqlite", options={"path": ":memory:"}),
            namespace="test",
        )
    backend = open_state(
        BackendConfiguration(
            backend="sqlite", options={"path": str(tmp_path / "state")}
        ),
        namespace="test",
    )
    with backend.transaction() as tx, pytest.raises(ValueError, match="limit"):
        tx.runs(limit=0)
