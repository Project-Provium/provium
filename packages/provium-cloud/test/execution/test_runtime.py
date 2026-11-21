import pytest
from cloud_fixtures import LocalBatch, finish, graph

from provium_cloud import CloudClient
from provium_cloud.deployment import CloudDeployment
from provium_cloud.execution.controller import Controller
from provium_cloud.execution.worker import execute_job


@pytest.fixture
def cloud(tmp_path):
    deployment = CloudDeployment.model_validate(
        {
            "provider": "aws",
            "region": "us-east-1",
            "state": {
                "backend": "sqlite",
                "options": {"path": str(tmp_path / "state.db")},
            },
            "artifacts": {
                "backend": "file",
                "options": {"location": str(tmp_path / "artifacts")},
            },
            "defaults": {"job_queue": "cpu", "job_definition": "worker:1"},
        }
    )
    executor = LocalBatch()
    with CloudClient(deployment, executor=executor) as client:
        yield client, Controller(client, executor=executor), executor


def test_pipeline_and_procedure_share_work_and_force(cloud, document, tmp_path):
    client, controller, executor = cloud
    first = client.enqueue_pipeline(graph(), {"document": str(document)})
    second = client.enqueue_procedure(
        "provium_text_pipeline_example.TokenizeV1", {"source": str(document)}
    )
    result = finish(client, controller, executor, first)
    assert result.status == "succeeded", result.error
    assert client.get_run(second.id).status == "succeeded"
    assert result.progress["completed"] == 2
    assert executor.submissions == 2
    client.fetch(first.id, "count", str(tmp_path / "count.pa"))
    assert (tmp_path / "count.pa").exists()
    cached = client.enqueue_pipeline(graph(), {"document": str(document)})
    assert finish(client, controller, executor, cached).status == "succeeded"
    assert executor.submissions == 2
    forced = client.enqueue_pipeline(graph(), {"document": str(document)}, force=True)
    duplicate = client.enqueue_pipeline(
        graph(), {"document": str(document)}, force=True
    )
    assert finish(client, controller, executor, forced).status == "succeeded"
    assert client.get_run(duplicate.id).status == "succeeded"
    assert executor.submissions == 4


def test_submission_failure_is_reconciled_without_resubmission(
    cloud, document, monkeypatch
):
    client, controller, executor = cloud
    run = client.enqueue_procedure(
        "provium_text_pipeline_example.TokenizeV1", {"source": str(document)}
    )
    submit = executor.submit

    def lost_response(*args):
        submit(*args)
        raise TimeoutError("lost response")

    monkeypatch.setattr(executor, "submit", lost_response)
    controller.tick()
    assert executor.submissions == 1
    controller.tick()
    executor.drain()
    assert client.get_run(run.id).status == "succeeded"
    assert executor.submissions == 1


def test_invalid_inputs_do_not_create_runs(cloud, document):
    client, _, _ = cloud
    with pytest.raises(ValueError, match="input"):
        client.enqueue_pipeline(graph(), {})
    assert client.list_runs().items == []
    with pytest.raises(ValueError, match="input"):
        client.enqueue_pipeline(graph(), {"document": str(document), "extra": 1})


def test_value_configuration_and_setup_inputs(cloud, test_catalog):
    client, controller, executor = cloud
    run = client.enqueue_procedure(
        "cloud.tests.Values", {"count": "3", "setup_scale": "2.5"}
    )
    result = finish(client, controller, executor, run)
    assert result.status == "succeeded", result.error
    assert client.lookup(result.tasks["procedure"].computation) == result.outputs


def test_procedure_without_outputs_is_still_executed(cloud, test_catalog):
    client, controller, executor = cloud
    run = client.enqueue_procedure("cloud.tests.Empty", {})
    assert finish(client, controller, executor, run).status == "succeeded"
    assert executor.submissions == 1


def test_cancellation_preserves_shared_jobs_and_supersedes_pending(cloud, document):
    client, controller, executor = cloud
    args = ("provium_text_pipeline_example.TokenizeV1", {"source": str(document)})
    first = client.enqueue_procedure(*args)
    second = client.enqueue_procedure(*args)
    controller.tick()
    client.cancel(first.id)
    controller.tick()
    assert executor.jobs["job-1"][3] == "RUNNABLE"
    forced = client.enqueue_procedure(*args, force=True)
    controller.tick()
    assert executor.jobs["job-1"][3] == "FAILED"
    result = finish(client, controller, executor, forced)
    assert result.status == "succeeded"
    assert client.get_run(second.id).status == "succeeded"
    assert client.get_run(first.id).status == "canceled"
    assert client.wait(first.id).status == "canceled"


def test_missing_output_is_recomputed(cloud, document):
    from pathlib import Path
    from urllib.parse import urlparse

    client, controller, executor = cloud
    args = ("provium_text_pipeline_example.TokenizeV1", {"source": str(document)})
    first = finish(client, controller, executor, client.enqueue_procedure(*args))
    Path(urlparse(first.outputs["destination"]["location"]).path).unlink()
    rerun = finish(client, controller, executor, client.enqueue_procedure(*args))
    assert rerun.status == "succeeded"
    assert executor.submissions == 2


def test_failure_blocks_downstream_and_retry_keeps_completed_tasks(
    cloud, document, monkeypatch
):
    client, controller, executor = cloud
    run = client.enqueue_pipeline(graph(), {"document": str(document)})
    controller.tick()
    task = client.get_run(run.id).tasks["tokens"]
    generation = client.coordinator.get(task.computation).generations[0]
    token = client.coordinator.claim(task.computation, 1, generation.attempt)
    client.coordinator.fail(task.computation, 1, token, "input failure")
    assert client.get_run(run.id).tasks["count"].status == "blocked"
    executor.jobs["job-1"][3] = "FAILED"
    retried = client.retry(run.id)
    assert finish(client, controller, executor, retried).status == "succeeded"


def test_worker_failure_is_recorded_and_does_not_publish(cloud, test_catalog):
    client, controller, executor = cloud
    run = client.enqueue_procedure(
        "cloud.tests.Values",
        {"count": 3, "setup_scale": 2},
        configuration={"fail": True},
    )
    controller.tick()
    with pytest.raises(RuntimeError, match="deliberate procedure failure"):
        executor.drain()
    failed = client.get_run(run.id)
    assert failed.status == "failed"
    assert not failed.outputs
    with pytest.raises(RuntimeError, match="deliberate procedure failure"):
        failed.raise_for_failure()


def test_worker_heartbeat_and_duplicate_claim(cloud, test_catalog, monkeypatch):
    import provium_cloud.execution.worker as runtime

    client, controller, executor = cloud
    run = client.enqueue_procedure(
        "cloud.tests.Values",
        {"count": 3, "setup_scale": 2},
        configuration={"delay": 0.5},
    )
    controller.tick()
    key, generation, deployment, _ = executor.jobs["job-1"]
    assert runtime.execute_job(
        deployment,
        key,
        generation.number,
        generation.attempt,
        job_id="job-1",
        lease_seconds=0.3,
    )
    assert not runtime.execute_job(
        deployment, key, generation.number, generation.attempt, job_id="job-1"
    )
    assert client.get_run(run.id).status == "succeeded"


def test_lost_heartbeat_stops_procedure(cloud, test_catalog, monkeypatch):
    from provium_cloud.state.coordination import Coordinator

    client, controller, executor = cloud
    run = client.enqueue_procedure(
        "cloud.tests.Values", {"count": 3, "setup_scale": 2}, configuration={"delay": 2}
    )
    controller.tick()
    key, generation, deployment, _ = executor.jobs["job-1"]
    monkeypatch.setattr(Coordinator, "heartbeat", lambda *args, **kwargs: False)
    with pytest.raises(RuntimeError, match="lease"):
        execute_job(
            deployment, key, 1, generation.attempt, job_id="job-1", lease_seconds=0.3
        )
    assert not client.get_run(run.id).outputs


def test_omitted_pipeline_values_and_optional_artifact(cloud, test_catalog):
    client, controller, executor = cloud
    pipeline = {
        "inputs": {"count": {"value_type": "int"}, "scale": {"value_type": "float"}},
        "nodes": {
            "value": {
                "procedure": "cloud.tests.Values",
                "inputs": {"count": "$inputs.count"},
                "setup_inputs": {"scale": "$inputs.scale"},
            }
        },
        "targets": {"result": "$nodes.value.result"},
    }
    result = finish(
        client,
        controller,
        executor,
        client.enqueue_pipeline(pipeline, {"count": 2, "scale": 1}),
    )
    assert result.status == "succeeded"
    optional = client.enqueue_procedure("cloud.tests.Optional", {})
    assert finish(client, controller, executor, optional).status == "succeeded"
    with pytest.raises(ValueError, match="incompatible input"):
        client.enqueue_procedure(
            "provium_text_pipeline_example.CountWordsV1",
            {"tokens": result.outputs["result"]["location"]},
        )


def test_cancel_retry_preserves_finished_tasks(cloud, document):
    client, controller, executor = cloud
    run = client.enqueue_pipeline(graph(), {"document": str(document)})
    controller.tick()
    executor.drain()
    assert client.get_run(run.id).tasks["tokens"].status == "completed"
    client.cancel(run.id)
    assert client.get_run(run.id).tasks["tokens"].status == "completed"
    client.retry(run.id)
    assert finish(client, controller, executor, run).status == "succeeded"
    assert executor.submissions == 2


def test_controller_uses_region_specific_providers_and_stops(cloud, monkeypatch):
    from threading import Event

    client, _, _ = cloud
    client.executor = None
    providers = []

    def factory(region):
        providers.append(region)
        return LocalBatch()

    monkeypatch.setattr("provium_cloud.execution.controller.BatchExecutor", factory)
    controller = Controller(client)
    assert controller.provider(client.deployment) is controller.provider(
        client.deployment
    )
    assert providers == ["us-east-1"]
    with pytest.raises(ValueError):
        controller.serve(interval=0)
    stop = Event()
    calls = []

    def tick():
        calls.append(1)
        stop.set()

    monkeypatch.setattr(controller, "tick", tick)
    controller.serve(stop=stop, interval=0.01)
    assert calls == [1]


def test_worker_rejects_mismatched_contract(cloud, document):
    client, controller, executor = cloud
    run = client.enqueue_procedure(
        "provium_text_pipeline_example.TokenizeV1", {"source": str(document)}
    )
    controller.tick()
    task = client.get_run(run.id).tasks["procedure"]
    with client.state.transaction() as tx:
        computation = tx.get_computation(task.computation)
        computation.generations[0].payload["record"] = {}
        tx.save_computation(computation)
    with pytest.raises(ValueError, match="contract"):
        executor.drain()
    assert client.get_run(run.id).status == "failed"
    assert client.lookup(task.computation) is None


def test_worker_stops_when_heartbeat_backend_is_unavailable(
    cloud, test_catalog, monkeypatch
):
    from provium_cloud.state.coordination import Coordinator

    client, controller, executor = cloud
    client.enqueue_procedure(
        "cloud.tests.Values", {"count": 3, "setup_scale": 2}, configuration={"delay": 2}
    )
    controller.tick()
    key, generation, deployment, _ = executor.jobs["job-1"]

    def unavailable(*args, **kwargs):
        raise ConnectionError("unavailable")

    monkeypatch.setattr(Coordinator, "heartbeat", unavailable)
    with pytest.raises(RuntimeError, match="lease"):
        execute_job(
            deployment, key, 1, generation.attempt, job_id="job-1", lease_seconds=0.3
        )


def test_worker_fences_publication_after_process_completion(
    cloud, document, monkeypatch
):
    from provium_cloud.execution import worker

    client, controller, executor = cloud
    run = client.enqueue_procedure(
        "provium_text_pipeline_example.TokenizeV1", {"source": str(document)}
    )
    controller.tick()
    original = worker._wait

    def lose_lease(process, inputs, outputs, lost, *args):
        original(process, inputs, outputs, lost, *args)
        lost.set()

    monkeypatch.setattr(worker, "_wait", lose_lease)
    with pytest.raises(RuntimeError, match="lease"):
        executor.drain()
    assert client.get_run(run.id).status == "failed"
    assert not client.get_run(run.id).outputs


def test_controller_advances_multiple_pages_of_runs(cloud, document):
    from provium_cloud.invocations.planning import prepare_pipeline

    client, controller, _ = cloud
    plan = prepare_pipeline(
        {
            "inputs": {"document": "provium_text_pipeline_example.DocumentV1"},
            "nodes": {},
            "targets": {"document": "$inputs.document"},
        },
        client.deployment,
    )
    inputs = {"document": client.artifacts.put(str(document))}
    runs = [client.runs.create(plan, inputs, client.deployment) for _ in range(101)]
    controller.tick()
    assert all(client.get_run(run.id).status == "succeeded" for run in runs)


def test_missing_passthrough_target_is_rejected(cloud):
    client, _, _ = cloud
    passthrough = {
        "inputs": {"document": "provium_text_pipeline_example.DocumentV1"},
        "nodes": {},
        "targets": {"document": "$inputs.document"},
    }
    with pytest.raises(ValueError, match="input"):
        client.enqueue_pipeline(passthrough, {})
    assert client.list_runs().items == []


def test_controller_service_recovers_from_transient_errors(cloud, monkeypatch):
    from threading import Event

    _, controller, _ = cloud
    stop = Event()
    calls = []

    def tick():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("transient provider failure")
        stop.set()

    monkeypatch.setattr(controller, "tick", tick)
    controller.serve(stop=stop, interval=0.01)
    assert len(calls) == 2
