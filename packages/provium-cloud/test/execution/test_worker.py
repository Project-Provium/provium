"""Abnormal child processes must never publish a successful result."""

from threading import Event
from time import monotonic
from unittest.mock import Mock

import pytest

from provium_cloud.execution.worker import _wait


@pytest.mark.parametrize("failure", ["timeout", "exited", "invalid-message"])
def test_worker_protocol_failures(failure):
    worker = Mock()
    worker.connection.poll.return_value = failure == "invalid-message"
    worker.connection.recv.return_value = ("invalid", None)
    worker.process.is_alive.return_value = False
    with pytest.raises((TimeoutError, RuntimeError)):
        _wait(
            worker,
            {},
            {},
            Event(),
            0.01,
            monotonic() - 10,
            1 if failure == "timeout" else None,
        )
