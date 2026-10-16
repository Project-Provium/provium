from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pipeline_fixtures import SOURCE

from provium_pipeline.workers import Worker, bindings, worker_main


def connection(commands, fail_send=None):
    result = Mock()
    result.recv.side_effect = commands
    result.send.side_effect = fail_send
    return result


def test_worker_protocol_success_and_invocation_error(tmp_path):
    channel = connection(
        [
            ({}, {"result": str(tmp_path / "result.pa")}),
            ({"unknown": "bad"}, {}),
            None,
        ]
    )
    worker_main(channel, SOURCE, {}, {})
    messages = [call.args[0] for call in channel.send.call_args_list]
    assert [kind for kind, _ in messages] == ["ready", "done", "error"]
    assert "KeyError" in messages[-1][1]
    assert (tmp_path / "result.pa").exists()
    channel.close.assert_called_once()


@pytest.mark.parametrize("error", [EOFError(), BrokenPipeError()])
def test_worker_closed_coordinator(error):
    channel = connection([error])
    worker_main(channel, SOURCE, {}, {})
    channel.close.assert_called_once()


@pytest.mark.parametrize("send_error", [None, BrokenPipeError()])
def test_worker_reports_setup_failure(monkeypatch, send_error):
    executor = Mock()
    executor.prepare.side_effect = ValueError("setup failed")
    monkeypatch.setattr("provium_pipeline.workers.ProcedureExecutor", lambda: executor)
    channel = connection([], send_error)
    worker_main(channel, SOURCE, {}, {})
    assert channel.send.call_args.args[0][0] == "error"
    channel.close.assert_called_once()


def test_binding_shapes():
    artifact = SimpleNamespace(
        bind_read=lambda value: ("read", value),
        bind_write=lambda value: ("write", value),
    )
    field = SimpleNamespace(artifact=SimpleNamespace(resolve=lambda: artifact))
    contract = SimpleNamespace(
        SetupInputs=None,
        Inputs=SimpleNamespace(fields={"many": field, "optional": field}),
    )
    procedure = SimpleNamespace(resolve_contract=lambda: contract)
    assert bindings(procedure, "SetupInputs", {}) == {}
    assert bindings(procedure, "Inputs", {"many": ["a", "b"], "optional": None}) == {
        "many": [("read", "a"), ("read", "b")],
        "optional": None,
    }


@pytest.mark.parametrize(
    "terminate, alive_after, send_error",
    [
        (True, False, None),
        (False, True, None),
        (False, False, BrokenPipeError()),
    ],
)
def test_worker_shutdown_paths(terminate, alive_after, send_error):
    process = Mock()
    process.is_alive.side_effect = [True, alive_after]
    channel = Mock()
    channel.send.side_effect = send_error
    Worker(process, channel).close(terminate=terminate)
    assert process.terminate.called is terminate
    assert process.kill.called is alive_after
    channel.close.assert_called_once()
    process.close.assert_called_once()
