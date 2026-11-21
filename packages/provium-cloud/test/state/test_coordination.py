"""Race-safe sharing, force generations, and fenced publication."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from provium_cloud.deployment import BackendConfiguration, CloudDeployment, JobSettings
from provium_cloud.invocations.models import InvocationPlan, PipelineGraph, TaskPlan


def create_run(runs, *, force=False, request_id=None):
    settings = JobSettings(job_queue="cpu", job_definition="worker:1")
    task = TaskPlan(
        procedure="example",
        record={},
        configuration={},
        inputs={},
        setup_inputs={},
        output_types={},
        settings=settings,
        force=force,
    )
    plan = InvocationPlan(
        graph=PipelineGraph(inputs={}, nodes={}, targets={}),
        tasks={"a": task},
        targets={},
    )
    deployment = CloudDeployment(
        provider="aws",
        region="us-east-1",
        state=BackendConfiguration(backend="custom"),
        artifacts=BackendConfiguration(backend="custom"),
    )
    return runs.create(plan, {}, deployment, request_id=request_id)


def admit(coordinator, run):
    return coordinator.admit(
        run.id, "a", "key", {"expected": {}, "procedure": "example"}
    )


def test_concurrent_runs_share_one_generation(stores):
    runs, coordinator = stores
    requests = [create_run(runs) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda run: admit(coordinator, run), requests))
    computation = coordinator.get("key")
    assert len(computation.generations) == 1
    generation = computation.generations[0]
    token = coordinator.claim("key", 1, generation.attempt)
    assert token
    assert coordinator.claim("key", 1, generation.attempt) is None
    assert coordinator.complete("key", 1, token, {})
    assert all(runs.refresh(run.id).status == "succeeded" for run in requests)


def test_cached_outputs_are_reused_and_force_is_coalesced(stores):
    runs, coordinator = stores
    original = create_run(runs)
    admit(coordinator, original)
    first = coordinator.get("key").generations[0]
    token = coordinator.claim("key", 1, first.attempt)
    assert coordinator.complete("key", 1, token, {})
    cached = create_run(runs)
    admit(coordinator, cached)
    assert runs.refresh(cached.id).tasks["a"].reused
    forced = [create_run(runs, force=True) for _ in range(3)]
    for run in forced:
        admit(coordinator, run)
    assert len(coordinator.get("key").generations) == 2
    assert coordinator.get("key").generations[-1].forced


def test_force_replaces_pending_and_waits_for_running_work(stores):
    runs, coordinator = stores
    old = create_run(runs)
    admit(coordinator, old)
    first = coordinator.get("key").generations[0]
    forced = create_run(runs, force=True)
    admit(coordinator, forced)
    assert coordinator.claim("key", 1, first.attempt) is None
    assert runs.refresh(old.id).tasks["a"].generation == 2
    second = coordinator.get("key").generations[1]
    token = coordinator.claim("key", 2, second.attempt)
    assert token
    assert coordinator.heartbeat("key", 2, token)
    assert coordinator.complete("key", 2, token, {})
    fresh = create_run(runs, force=True)
    admit(coordinator, fresh)
    assert len(coordinator.get("key").generations) == 3


def test_force_running_normal_work_creates_one_successor(stores):
    runs, coordinator = stores
    old = create_run(runs)
    admit(coordinator, old)
    first = coordinator.get("key").generations[0]
    token = coordinator.claim("key", 1, first.attempt)
    fresh = create_run(runs, force=True)
    admit(coordinator, fresh)
    second = coordinator.get("key").generations[1]
    assert coordinator.claim("key", 2, second.attempt) is None
    assert coordinator.complete("key", 1, token, {})
    assert coordinator.claim("key", 2, second.attempt)


def test_expired_owner_cannot_renew_or_publish(stores):
    runs, coordinator = stores
    run = create_run(runs)
    admit(coordinator, run)
    first = coordinator.get("key").generations[0]
    token = coordinator.claim("key", 1, first.attempt)
    with coordinator.backend.transaction() as tx:
        computation = tx.get_computation("key")
        computation.generations[0].lease_until = tx.now - 1
        tx.save_computation(computation)
    assert not coordinator.heartbeat("key", 1, token)
    assert not coordinator.complete("key", 1, token, {})


def test_request_id_and_shared_cancellation(stores):
    runs, coordinator = stores
    first = create_run(runs, request_id="request")
    assert create_run(runs, request_id="request").id == first.id
    with pytest.raises(ValueError, match="request"):
        create_run(runs, request_id="request", force=True)
    second = create_run(runs)
    admit(coordinator, first)
    admit(coordinator, second)
    runs.cancel(first.id)
    coordinator.cancel_orphans("key")
    assert coordinator.get("key").generations[0].status == "queued"
    runs.cancel(second.id)
    coordinator.cancel_orphans("key")
    assert coordinator.get("key").generations[0].status == "canceled"


def test_failure_retry_and_pagination(stores):
    runs, coordinator = stores
    run = create_run(runs)
    admit(coordinator, run)
    first = coordinator.get("key").generations[0]
    token = coordinator.claim("key", 1, first.attempt)
    assert coordinator.fail("key", 1, token, "broken")
    assert runs.refresh(run.id).status == "failed"
    assert runs.retry(run.id).status == "queued"
    admit(coordinator, runs.get(run.id))
    assert len(coordinator.get("key").generations) == 2
    other = create_run(runs)
    page = runs.list(limit=1)
    assert page.items[0].id == run.id
    assert runs.list(cursor=page.next_cursor).items[0].id == other.id


def test_claim_and_publication_boundaries(stores):
    runs, coordinator = stores
    run = create_run(runs)
    admit(coordinator, run)
    assert admit(coordinator, run).generation == 1
    generation = coordinator.get("key").generations[0]
    assert coordinator.claim("key", 1, "wrong-attempt") is None
    for number in (0, -1, 2):
        with pytest.raises(ValueError, match="generation"):
            coordinator.claim("key", number, generation.attempt)
    for ttl in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="duration"):
            coordinator.claim("key", 1, generation.attempt, ttl=ttl)
        with pytest.raises(ValueError, match="duration"):
            coordinator.heartbeat("key", 1, "owner", ttl=ttl)
    token = coordinator.claim("key", 1, generation.attempt)
    with pytest.raises(ValueError, match="outputs"):
        coordinator.complete("key", 1, token, {"unexpected": {}})
    assert not coordinator.fail("key", 1, "not-owner", "failure")


def test_force_joins_running_force_and_late_retired_worker_is_rejected(stores):
    runs, coordinator = stores
    old = create_run(runs)
    admit(coordinator, old)
    retired = coordinator.get("key").generations[0]
    forced = create_run(runs, force=True)
    admit(coordinator, forced)
    coordinator.cancel_orphans("key")
    assert coordinator.get("key").generations[1].status == "queued"
    assert coordinator.claim("key", 1, retired.attempt, job_id="late") is None
    assert coordinator.get("key").generations[0].cancellation_pending
    from provium_cloud.state.jobs import JobStore

    JobStore(coordinator.backend).observed("key", 1, {"late": "FAILED"})
    generation = coordinator.get("key").generations[1]
    assert coordinator.claim("key", 2, generation.attempt)
    admit(coordinator, create_run(runs, force=True))
    assert len(coordinator.get("key").generations) == 2


def test_run_validation_and_terminal_operations(stores):
    runs, coordinator = stores
    with pytest.raises(LookupError):
        runs.get("missing")
    with pytest.raises(ValueError):
        create_run(runs, request_id=" ")
    with pytest.raises(ValueError):
        runs.list(limit=0)
    run = create_run(runs)
    with pytest.raises(ValueError):
        runs.retry(run.id)
    assert runs.list(statuses=[], input_set_id=run.input_set_id).items == []
    assert len(runs.list(input_set_id=run.input_set_id).items) == 1
    admit(coordinator, run)
    generation = coordinator.get("key").generations[0]
    owner = coordinator.claim("key", 1, generation.attempt)
    coordinator.complete("key", 1, owner, {})
    succeeded = runs.refresh(run.id)
    succeeded.raise_for_failure()
    assert runs.cancel(run.id).status == "succeeded"
