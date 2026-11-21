from test_coordination import admit, create_run

from provium_cloud.state.jobs import JobStore


def test_submission_reservation_and_late_ack(stores):
    runs, coordinator = stores
    run = create_run(runs)
    admit(coordinator, run)
    jobs = JobStore(coordinator.backend)
    token = jobs.reserve("key", 1)
    assert token
    assert jobs.reserve("key", 1) is None
    jobs.submitted("key", 1, token, ["batch-id"])
    assert coordinator.get("key").generations[0].status == "submitted"
    forced = create_run(runs, force=True)
    admit(coordinator, forced)
    jobs.submitted("key", 1, token, ["late-id"])
    first = coordinator.get("key").generations[0]
    assert first.status == "superseded"
    assert first.cancellation_pending
    assert first.job_ids == ["batch-id", "late-id"]
    assert jobs.reserve("key", 2) is None
    jobs.observed("key", 1, {"batch-id": "FAILED", "late-id": "FAILED"})
    assert jobs.reserve("key", 2)


def test_expired_worker_needs_provider_confirmation(stores):
    runs, coordinator = stores
    run = create_run(runs)
    admit(coordinator, run)
    generation = coordinator.get("key").generations[0]
    coordinator.claim("key", 1, generation.attempt, job_id="job")
    with coordinator.backend.transaction() as tx:
        computation = tx.get_computation("key")
        computation.generations[0].lease_until = tx.now - 1
        tx.save_computation(computation)
    jobs = JobStore(coordinator.backend)
    jobs.observed("key", 1, {"job": "RUNNING"})
    assert coordinator.get("key").generations[0].cancellation_pending
    jobs.observed("key", 1, {"job": "FAILED"})
    assert not coordinator.get("key").generations[0].cancellation_pending


def test_provider_success_requires_committed_outputs(stores):
    runs, coordinator = stores
    run = create_run(runs)
    admit(coordinator, run)
    jobs = JobStore(coordinator.backend)
    token = jobs.reserve("key", 1)
    jobs.submitted("key", 1, token, ["job"])
    jobs.observed("key", 1, {"job": "SUCCEEDED"})
    assert runs.refresh(run.id).status == "failed"


def test_ambiguous_submission_is_held_until_reservation_expires(stores):
    runs, coordinator = stores
    admit(coordinator, create_run(runs))
    jobs = JobStore(coordinator.backend)
    owner = jobs.reserve("key", 1)
    jobs.submitted("key", 1, owner, [])
    assert coordinator.get("key").generations[0].status == "submitting"
    assert jobs.reserve("key", 1) is None
    with coordinator.backend.transaction() as tx:
        computation = tx.get_computation("key")
        computation.generations[0].submit_until = tx.now - 1
        tx.save_computation(computation)
    assert jobs.reserve("key", 1) != owner


def test_unknown_late_submission_cannot_hold_successor_forever(stores):
    runs, coordinator = stores
    admit(coordinator, create_run(runs))
    jobs = JobStore(coordinator.backend)
    jobs.reserve("key", 1)
    admit(coordinator, create_run(runs, force=True))
    with coordinator.backend.transaction() as tx:
        computation = tx.get_computation("key")
        computation.generations[0].submit_until = tx.now - 1
        tx.save_computation(computation)
    jobs.observed("key", 1, {})
    assert jobs.reserve("key", 2)


def test_invalidating_non_successful_work_is_a_noop(stores):
    runs, coordinator = stores
    admit(coordinator, create_run(runs))
    jobs = JobStore(coordinator.backend)
    jobs.invalidate("key", 1, "missing")
    assert coordinator.get("key").generations[0].status == "queued"
