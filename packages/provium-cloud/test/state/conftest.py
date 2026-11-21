import pytest

from provium_cloud.deployment import BackendConfiguration
from provium_cloud.state import open_state
from provium_cloud.state.coordination import Coordinator
from provium_cloud.state.runs import RunStore


@pytest.fixture
def stores(tmp_path):
    backend = open_state(
        BackendConfiguration(
            backend="sqlite", options={"path": str(tmp_path / "state.db")}
        ),
        namespace="test",
    )
    return RunStore(backend), Coordinator(backend)
