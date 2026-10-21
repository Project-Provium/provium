"""Management commands select only their own persistence operation."""

import json
from unittest.mock import Mock

import pytest
from provium.cli import run

from provium_pipeline import definitions
from provium_pipeline.models import PipelineDefinition
from provium_pipeline.persistence import factory


@pytest.fixture
def backend(monkeypatch):
    backend = Mock()
    backend.get.return_value = {
        "kind": "PipelineConfiguration",
        "name": "empty",
        "inputs": {},
        "nodes": {},
        "outputs": {},
    }
    monkeypatch.setattr(factory, "open_backend", lambda connection: backend)
    return backend


@pytest.mark.parametrize(
    ("arguments", "method", "parameters", "result", "output"),
    [
        (["list", "profiles"], "registered", ("ComputeProfile",), [], []),
        (["queue", "empty"], "queue_info", ("empty",), [], []),
        (
            ["enqueue", "empty", "--input", "x=source"],
            "enqueue",
            ("empty", {"x": "source"}),
            7,
            {"id": 7},
        ),
        (["retry", "empty"], "retry", ("empty",), 3, {"reset": 3}),
        (
            ["unregister", "empty"],
            "unregister",
            ([{"kind": "PipelineConfiguration", "name": "empty"}],),
            {},
            {},
        ),
    ],
)
def test_management_dispatch(
    backend, capsys, arguments, method, parameters, result, output
):
    getattr(backend, method).return_value = result
    assert run(["pipeline", *arguments, "--database", "unused"]) == 0
    keywords = (
        {"queue_group": "default"}
        if method in {"queue_info", "enqueue", "retry"}
        else {}
    )
    getattr(backend, method).assert_called_once_with(*parameters, **keywords)
    assert json.loads(capsys.readouterr().out) == output
    assert {call[0] for call in backend.method_calls} <= {"get", method}


def test_register_resolves_definition_once(backend, tmp_path, monkeypatch, capsys):
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        "kind: PipelineConfiguration\nname: empty\ninputs: {}\nnodes: {}\noutputs: {}\n"
    )
    definition = PipelineDefinition({}, {})
    loader = Mock(return_value=definition)
    monkeypatch.setattr(definitions, "load_definition", loader)
    assert run(["pipeline", "register", "-f", str(path), "--database", "unused"]) == 0
    loader.assert_called_once_with("empty", None)
    assert backend.register.call_args.kwargs["definitions"] == {"empty": definition}
    assert json.loads(capsys.readouterr().out) == {"registered": 1}


@pytest.mark.parametrize("command", ["register", "unregister", "enqueue", "retry"])
def test_invalid_documents_do_not_open_backend(command, tmp_path, monkeypatch):
    path = tmp_path / "bad.yaml"
    path.write_text("kind: PipelineConfiguration\nname: bad\n")
    opener = Mock(side_effect=AssertionError("premature backend import"))
    monkeypatch.setattr(factory, "open_backend", opener)
    assert run(["pipeline", command, "-f", str(path), "--database", "unused"]) == 2
    opener.assert_not_called()


@pytest.mark.parametrize("command", ["enqueue", "retry"])
def test_management_selects_pipeline_from_file(backend, tmp_path, capsys, command):
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        "kind: PipelineConfiguration\nname: empty\ninputs: {}\nnodes: {}\noutputs: {}\n"
    )
    backend.enqueue.return_value = 7
    backend.retry.return_value = 3
    assert run(["pipeline", command, "-f", str(path), "--database", "unused"]) == 0
    if command == "enqueue":
        backend.enqueue.assert_called_once_with("empty", {}, queue_group="default")
        assert json.loads(capsys.readouterr().out) == {"id": 7}
    else:
        backend.retry.assert_called_once_with("empty", queue_group="default")
        assert json.loads(capsys.readouterr().out) == {"reset": 3}
    backend.get.assert_not_called()


@pytest.mark.parametrize("command", ["register", "unregister"])
def test_empty_documents_do_not_open_backend(tmp_path, monkeypatch, capsys, command):
    path = tmp_path / "empty.yaml"
    path.write_text("# no documents\n")
    opener = Mock()
    monkeypatch.setattr(factory, "open_backend", opener)
    assert run(["pipeline", command, "-f", str(path), "--database", "unused"]) == 2
    assert "requires" in capsys.readouterr().err
    opener.assert_not_called()


@pytest.mark.parametrize("command", ["inspect", "status"])
@pytest.mark.parametrize("idle_wait", ["-1", "nan", "inf"])
def test_invalid_idle_wait_reports_error(command, idle_wait, capsys):
    assert run(["pipeline", command, "-f", "unused", "--idle-wait", idle_wait]) == 2
    assert "idle wait must be finite and nonnegative" in capsys.readouterr().err


@pytest.mark.parametrize("selector", ["default", "nodes.tokenize"])
def test_inspect_resolves_worker_overrides(tmp_path, capsys, selector):
    from test_commands import config

    assert (
        run(
            [
                "pipeline",
                "inspect",
                "-f",
                config(tmp_path),
                "--workers",
                f"{selector}=2",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["settings"]["tokenize"]["workers"] == 2
