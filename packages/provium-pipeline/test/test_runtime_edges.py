import queue
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from provium_text_pipeline_example.artifact import DOCUMENT, TOKENS
from test_runtime import configuration
from test_store import make_artifact

from provium_pipeline import (
    ArtifactStore,
    ComputeProfile,
    ExecutionContext,
    InputSet,
    Outcome,
    PipelineDefinition,
)
from provium_pipeline.persistence.interface import Lease


def context(tmp_path, **options):
    return ExecutionContext(
        configuration(), ArtifactStore(str(tmp_path / "store")), **options
    )


@pytest.mark.parametrize(
    "options",
    [
        {"only": ["invalid"]},
        {"only": ["nodes.missing"]},
        {"workers": [{"default": 1, "nodes.tokenize": 2}]},
        {"workers": [{"nodes.missing": 1}]},
        {"profile": ComputeProfile(name="test", workers=[{"tags.missing": 2}])},
        {
            "profile": ComputeProfile(
                name="test", workers=[{"pipelines.tokenize.nodes.missing": 2}]
            )
        },
        {
            "definition": PipelineDefinition(
                {"document": DOCUMENT}, {"tokens": TOKENS}
            ),
            "profile": ComputeProfile(name="not-allowed"),
        },
    ],
)
def test_invalid_execution_settings(tmp_path, options):
    with pytest.raises(ValueError):
        context(tmp_path, **options)


def test_specific_workers_and_context_lifecycle(tmp_path):
    with context(tmp_path, workers=[{"nodes.tokenize": 2}]) as execution:
        assert execution.settings["tokenize"].workers == 2
        execution.start([])
        with pytest.raises(RuntimeError, match="once"):
            execution.start([])
        with pytest.raises(RuntimeError, match="unused"):
            execution.inspect_inputs([])
    with pytest.raises(ValueError, match="application"):
        with context(tmp_path):
            raise ValueError("application error")


def test_registered_definition_cannot_be_changed(tmp_path):
    backend = Mock()
    definition = PipelineDefinition({"document": DOCUMENT}, {"tokens": TOKENS})
    backend.definition.return_value = definition
    with context(tmp_path, backend=backend, definition=definition):
        pass
    with pytest.raises(ValueError, match="snapshot"):
        context(tmp_path, backend=backend, definition=PipelineDefinition({}, {}))


def test_input_artifact_objects_and_mutation_are_checked(tmp_path):
    store = ArtifactStore(str(tmp_path / "store"))
    source = make_artifact(tmp_path / "source.pa")
    artifact = store.inspect(str(source))
    values = [
        artifact,
        replace(artifact, identity="changed"),
        replace(artifact, fingerprint={}),
        {
            "location": artifact.location,
            "identity": "changed",
            "fingerprint": artifact.fingerprint,
        },
        {
            "location": artifact.location,
            "identity": artifact.identity,
            "fingerprint": {},
        },
    ]
    for index, value in enumerate(values):
        with context(tmp_path) as execution:
            reports = execution.inspect_inputs([InputSet({"document": value})])
        assert reports[0]["state"] == ("partial" if index == 0 else "failed")
    with context(tmp_path) as execution:
        execution.plan.input_types["document"] = "wrong"
        assert (
            execution.inspect_inputs([InputSet({"document": artifact})])[0]["state"]
            == "failed"
        )


def test_status_reports_blocked_and_corrupt_artifacts(tmp_path):
    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    with context(tmp_path) as execution:
        execution.start([item])
    output = execution.outcomes[0].outputs["tokens"]
    from pathlib import Path

    Path(output.path).write_bytes(b"corrupt")
    with context(tmp_path, only=["nodes.tokenize"]) as inspector:
        report = inspector.inspect_inputs([item])[0]
        assert report["nodes"]["tokenize"]["state"] == "invalid"
        report = inspector.inspect_inputs([InputSet({})])[0]
        assert report["nodes"]["tokenize"]["state"] == "blocked"


def test_queue_snapshot_tampering_is_rejected(tmp_path):
    with context(tmp_path) as execution:
        with pytest.raises(ValueError, match="pipeline snapshot"):
            execution._load_page([{"id": 1, "snapshot": {}}])
        with pytest.raises(ValueError, match="definition"):
            execution._load_page(
                [
                    {
                        "id": 1,
                        "snapshot": execution.plan.configuration.model_dump(
                            mode="json"
                        ),
                        "definition": {},
                    }
                ]
            )


def test_notification_overflow_and_duplicate_delivery(tmp_path):
    item = InputSet({})
    with context(tmp_path) as execution:
        execution._notifications = queue.Queue(maxsize=1)
        event = Outcome(item, "partial")
        execution._notify(event)
        execution._notify(event)
        assert execution._notifications.get_nowait() is event
        assert execution._notifications.empty()


@pytest.mark.parametrize("renew", [True, False, OSError("disconnected")])
def test_lease_renewal_and_loss(tmp_path, renew):
    backend = Mock()
    backend.definition.return_value = None
    if isinstance(renew, Exception):
        backend.renew.side_effect = renew
    else:
        backend.renew.return_value = renew
    with context(tmp_path, backend=backend) as execution:
        lease = Lease("store", "key", "owner", 1, "run")
        execution._leases["key"] = lease
        job = SimpleNamespace(key="key", interrupted=False)
        worker = SimpleNamespace(job=job, process=Mock())
        worker.process.is_alive.return_value = True
        execution._workers = [worker, SimpleNamespace(job=None, process=Mock())]
        execution._stop_monitor = Mock()
        execution._stop_monitor.wait.side_effect = [False, True]
        execution._heartbeat()
        assert job.interrupted is (renew is not True)
        assert worker.process.terminate.called is (renew is not True)
        execution._workers.clear()


def test_heartbeat_without_database_and_expired_monitor(tmp_path):
    with context(
        tmp_path, profile=ComputeProfile(name="test", hard_time_limit=1)
    ) as execution:
        execution._stop_monitor = Mock()
        execution._stop_monitor.wait.side_effect = [False, True]
        execution._heartbeat()
        accepted = SimpleNamespace(accepted=True, interrupted=False)
        active = SimpleNamespace(accepted=False, interrupted=False)
        workers = [
            SimpleNamespace(job=job, process=Mock()) for job in (accepted, active, None)
        ]
        workers[1].process.is_alive.return_value = False
        execution._workers = workers
        execution._stop_monitor.wait.side_effect = [False, True]
        execution._monitor()
        assert not accepted.interrupted
        assert active.interrupted
        assert workers[2].process.terminate.called
        execution._workers.clear()
        assert execution._active_time(None) == 0


def test_node_timer_accumulates_across_switches_and_idle(tmp_path, monkeypatch):
    with context(
        tmp_path, profile=ComputeProfile(name="test", soft_time_limits=[{"default": 5}])
    ) as execution:
        clock = [10.0]
        monkeypatch.setattr("provium_pipeline.runtime.time.monotonic", lambda: clock[0])
        execution._started = 10
        execution._active = "tokenize"
        execution._active_start = 10
        clock[0] = 13
        execution._unload_node()
        clock[0] = 100
        assert execution._active_time("tokenize") == 3
        execution._active = "tokenize"
        execution._active_start = 100
        clock[0] = 102
        assert not execution._allowed("tokenize")
        execution._unload_node()


def test_repeated_and_optional_bindings_match_core_provenance(tmp_path):
    from pipeline_fixtures import COLLECTION

    from provium_pipeline import PipelineConfiguration

    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    for index, bindings in enumerate(
        (
            {"sources": ["$inputs.document", "$inputs.document"], "optional": None},
            {},
        )
    ):
        graph = PipelineConfiguration(
            inputs={"document": "provium_text_pipeline_example.DocumentV1"}
            if bindings
            else {},
            name="collection",
            nodes={"collect": {"procedure": COLLECTION.identifier, "inputs": bindings}},
            outputs={"result": "$nodes.collect.result"},
        )
        for _ in range(2):
            with ExecutionContext(
                graph,
                ArtifactStore(str(tmp_path / str(index))),
                procedures={COLLECTION.identifier: COLLECTION},
            ) as execution:
                execution.start([item if bindings else InputSet({})])
            assert execution.outcomes[0].state == "completed"


def test_cancelled_run_and_idle_wait(tmp_path):
    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    with context(tmp_path) as execution:

        def items():
            yield item
            execution.cancel()

        execution.start(items())
    assert execution.outcomes[0].state == "cancelled"
    assert not execution._allowed("tokenize")
    with context(tmp_path, idle_wait=0.02) as execution:
        execution.start([])
    assert execution.finished


def test_executor_failure_is_reported_on_close(tmp_path, monkeypatch):
    execution = context(tmp_path)
    monkeypatch.setattr(
        execution, "_run_body", Mock(side_effect=OSError("executor broke"))
    )
    with pytest.raises(RuntimeError, match="executor failed"):
        with execution:
            execution.start([])
    assert isinstance(execution.error, OSError)


def test_unresponsive_callback_shutdown_is_bounded(tmp_path, monkeypatch, capsys):
    with context(tmp_path) as execution:
        fake_thread = Mock()
        fake_thread.is_alive.return_value = True
        monkeypatch.setattr(
            "provium_pipeline.runtime.threading.Thread", lambda **kwargs: fake_thread
        )
        execution._notifications = Mock()
        execution._notifications.put.side_effect = queue.Full
        execution._run([])
        assert execution.finished
        assert execution.error is None
        assert "shutdown grace" in capsys.readouterr().err


def test_duplicate_step_messages_notify_once(tmp_path):
    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    with context(tmp_path) as execution:
        execution._add(item)
        job = execution._job(execution._states[0], "tokenize")
        execution._accept(job, {}, False)
        execution._accept(job, {}, False)
        assert execution._notifications.qsize() == 1
    backend = Mock()
    backend.definition.return_value = None
    with context(tmp_path, backend=backend) as execution:
        execution._add(item)
        job = execution._job(execution._states[0], "tokenize")
        job.lease = Lease("store", job.key, "owner", 1, "run")
        execution._failure(job, "failed")
        execution._failure(job, "duplicate")
        assert execution._notifications.qsize() == 1
        backend.fail.assert_called()


def test_publication_rejects_lost_lease_or_unacknowledged_write(tmp_path):
    backend = Mock()
    backend.definition.return_value = None
    backend.complete.return_value = False
    with context(tmp_path, backend=backend) as execution:
        job = SimpleNamespace(
            key="a" * 64, lease=Lease("store", "a" * 64, "owner", 1, "run")
        )
        with pytest.raises(RuntimeError, match="lease lost"):
            execution._publish(job, {})
        backend.complete.side_effect = OSError("commit failed")
        backend.result.return_value = None
        with pytest.raises(OSError, match="commit failed"):
            execution._publish(job, {})


def test_corrupt_cache_is_failure_not_recomputation(tmp_path):
    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    with context(tmp_path) as execution:
        execution.start([item])
    from pathlib import Path

    Path(execution.outcomes[0].outputs["tokens"].path).write_bytes(b"corrupt")
    with context(tmp_path, verify_artifacts=True) as execution:
        execution.start([item])
    assert execution.outcomes[0].state == "failed"
    assert execution.worker_starts == 0


def test_time_expiry_between_selection_and_dispatch(tmp_path, monkeypatch):
    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    with context(tmp_path) as execution:
        execution._add(item)
        original = execution._ready

        def expire(node=None):
            jobs = original(node)
            if node is not None:
                execution.cancel()
            return jobs

        monkeypatch.setattr(execution, "_ready", expire)
        execution.start([item])
    assert execution.worker_starts == 0
    assert not execution._states[0].failed


@pytest.mark.parametrize("kind", ["ready", "done"])
def test_idle_worker_messages_cannot_create_duplicate_outcomes(
    tmp_path, monkeypatch, kind
):
    with context(tmp_path) as execution:
        item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
        execution._add(item)
        job = execution._job(execution._states[0], "tokenize")
        worker = Mock()
        worker.job = None
        worker.connection.recv.return_value = (kind, {})
        worker.process.is_alive.return_value = False
        execution._active = "tokenize"
        execution._active_start = time.monotonic()
        execution._resident_setup = job.setup_key
        execution._workers = [worker]
        monkeypatch.setattr(execution, "_ready", lambda node: [])
        execution._run_node(job)
        assert execution._notifications.empty()
        worker.close.assert_called_once()


def test_dispatch_to_already_dead_worker(tmp_path):
    with context(tmp_path) as execution:
        job = SimpleNamespace(
            directory=str(tmp_path),
            expected={},
            inputs={},
            key="key",
            interrupted=False,
        )
        worker = Mock(job=job)
        worker.connection.send.side_effect = BrokenPipeError()
        worker.process.is_alive.return_value = False
        execution._send(worker)
        assert job.interrupted
        worker.process.terminate.assert_not_called()


@pytest.mark.parametrize("interrupt", [False, True])
def test_publication_failure_does_not_accept_result(tmp_path, monkeypatch, interrupt):
    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    with context(tmp_path) as execution:
        original = execution.store.stage

        def stage(*args):
            if not interrupt:
                raise OSError("staging failed")
            outputs = original(*args)
            execution._workers[0].job.interrupted = True
            return outputs

        monkeypatch.setattr(execution.store, "stage", stage)
        execution.start([item])
    assert execution.outcomes[0].state == "failed"
    assert execution.error is None


def test_worker_disappears_without_a_message(tmp_path, monkeypatch):
    from provium_pipeline.workers import Worker

    original = Worker.start

    def start(*args):
        worker = original(*args)
        worker.process.terminate()
        wrapper = Mock(wraps=worker.connection)
        wrapper.poll.return_value = False
        worker.connection = wrapper
        return worker

    monkeypatch.setattr(Worker, "start", start)
    item = InputSet({"document": str(make_artifact(tmp_path / "source.pa"))})
    with context(tmp_path) as execution:
        execution.start([item])
    assert execution.outcomes[0].state == "failed"
    assert execution.error is None


def test_heartbeat_does_not_terminate_an_already_dead_worker(tmp_path):
    backend = Mock()
    backend.definition.return_value = None
    backend.renew.return_value = False
    with context(tmp_path, backend=backend) as execution:
        execution._leases["key"] = Lease("store", "key", "owner", 1, "run")
        worker = Mock()
        worker.job.key = "key"
        worker.process.is_alive.return_value = False
        execution._workers = [worker]
        execution._stop_monitor = Mock()
        execution._stop_monitor.wait.side_effect = [False, True]
        execution._heartbeat()
        assert worker.job.interrupted
        worker.process.terminate.assert_not_called()
        execution._workers.clear()
