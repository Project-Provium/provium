"""Atomic deduplication, forced generations, and fenced worker ownership."""

from math import isfinite
from typing import Any
from uuid import uuid4

from provium_cloud.artifacts import ArtifactReference
from provium_cloud.state.interface import StateBackend, StateTransaction
from provium_cloud.state.models import Computation, Generation, Task
from provium_cloud.state.runs import ACTIVE_RUNS, require_run

ACTIVE = {"queued", "submitting", "submitted", "running"}


def require_computation(tx: StateTransaction, key: str) -> Computation:
    computation = tx.get_computation(key)
    if computation is None:
        raise LookupError(f"unknown computation: {key}")
    return computation


def require_generation(computation: Computation, number: int) -> Generation:
    if not 1 <= number <= len(computation.generations):
        raise ValueError("invalid computation generation")
    return computation.generations[number - 1]


def owns(generation: Generation, owner: str, now: float) -> bool:
    return (
        generation.status == "running"
        and generation.owner == owner
        and generation.lease_until > now
    )


class Coordinator:
    def __init__(self, backend: StateBackend) -> None:
        self.backend = backend

    def get(self, key: str) -> Computation:
        with self.backend.transaction() as tx:
            return require_computation(tx, key)

    def admit(self, run_id: str, node: str, key: str, payload: dict[str, Any]) -> Task:
        with self.backend.transaction() as tx:
            run = require_run(tx, run_id)
            task = run.tasks[node]
            if run.status not in ACTIVE_RUNS or task.status != "waiting":
                return task
            computation = tx.get_computation(key) or Computation(key=key)
            active = [g for g in computation.generations if g.status in ACTIVE]
            successful = [g for g in computation.generations if g.status == "succeeded"]
            generation = active[-1] if active else None
            if task.plan.force:
                if (
                    generation is not None
                    and generation.forced
                    and generation.status in ("queued", "running")
                ):
                    if generation.status == "queued":
                        generation.settings, generation.payload = (
                            task.plan.settings,
                            payload,
                        )
                else:
                    if generation is not None and generation.status != "running":
                        generation.cancellation_pending = (
                            bool(generation.job_ids)
                            or generation.status == "submitting"
                        )
                        generation.status = "superseded"
                    generation = None
            elif generation is None and successful:
                generation = successful[-1]
                task.reused = True
            if generation is None:
                generation = Generation(
                    number=len(computation.generations) + 1,
                    attempt=uuid4().hex,
                    payload=payload,
                    settings=task.plan.settings,
                    forced=task.plan.force,
                    created_at=tx.now,
                    updated_at=tx.now,
                )
                computation.generations.append(generation)
            task.computation, task.generation, task.status = (
                key,
                generation.number,
                "queued",
            )
            run.updated_at = tx.now
            tx.save_computation(computation)
            tx.save_run(run)
            return task

    def claim(
        self,
        key: str,
        number: int,
        attempt: str,
        *,
        job_id: str | None = None,
        ttl: float = 60,
    ) -> str | None:
        if not isfinite(ttl) or ttl <= 0:
            raise ValueError("lease duration must be positive")
        with self.backend.transaction() as tx:
            computation = require_computation(tx, key)
            generation = require_generation(computation, number)
            if generation.attempt != attempt:
                return None
            if job_id and job_id not in generation.job_ids:
                generation.job_ids.append(job_id)
                if generation.status in ("superseded", "canceled", "failed"):
                    generation.cancellation_pending = True
                tx.save_computation(computation)
            if generation.status not in ("queued", "submitting", "submitted"):
                return None
            if any(
                g.status == "running" or g.cancellation_pending
                for g in computation.generations
                if g.number != number
            ):
                return None
            generation.status = "running"
            generation.owner = uuid4().hex
            generation.lease_until = tx.now + ttl
            generation.updated_at = tx.now
            tx.save_computation(computation)
            return generation.owner

    def heartbeat(self, key: str, number: int, owner: str, *, ttl: float = 60) -> bool:
        if not isfinite(ttl) or ttl <= 0:
            raise ValueError("lease duration must be positive")
        with self.backend.transaction() as tx:
            computation = require_computation(tx, key)
            generation = require_generation(computation, number)
            if not owns(generation, owner, tx.now):
                return False
            generation.lease_until, generation.updated_at = tx.now + ttl, tx.now
            tx.save_computation(computation)
            return True

    def complete(
        self, key: str, number: int, owner: str, outputs: dict[str, ArtifactReference]
    ) -> bool:
        with self.backend.transaction() as tx:
            computation = require_computation(tx, key)
            generation = require_generation(computation, number)
            if not owns(generation, owner, tx.now):
                return False
            expected = generation.payload["expected"]
            if set(outputs) != set(expected) or any(
                outputs[name]["fingerprint"]["provenance"] != provenance
                for name, provenance in expected.items()
            ):
                raise ValueError("outputs do not match the admitted computation")
            generation.outputs, generation.status = outputs, "succeeded"
            generation.lease_until, generation.updated_at = 0, tx.now
            tx.save_computation(computation)
            return True

    def fail(self, key: str, number: int, owner: str, error: str) -> bool:
        with self.backend.transaction() as tx:
            computation = require_computation(tx, key)
            generation = require_generation(computation, number)
            if not owns(generation, owner, tx.now):
                return False
            generation.status, generation.error, generation.updated_at = (
                "failed",
                error,
                tx.now,
            )
            generation.lease_until = 0
            tx.save_computation(computation)
            return True

    def cancel_orphans(self, key: str) -> None:
        with self.backend.transaction() as tx:
            computation = require_computation(tx, key)
            needed: set[int] = set()
            cursor = ""
            while runs := tx.runs(statuses=ACTIVE_RUNS, after=cursor):
                for run in runs:
                    for task in run.tasks.values():
                        if (
                            task.computation == key
                            and task.generation is not None
                            and task.status != "completed"
                        ):
                            generation = computation.generations[task.generation - 1]
                            while generation.status == "superseded":
                                generation = computation.generations[generation.number]
                            needed.add(generation.number)
                cursor = runs[-1].id
            for generation in computation.generations:
                if generation.number not in needed and generation.status in ACTIVE:
                    generation.cancellation_pending = (
                        bool(generation.job_ids) or generation.status == "submitting"
                    )
                    generation.status, generation.error = (
                        "canceled",
                        "no active run requires this work",
                    )
                    generation.updated_at = tx.now
            tx.save_computation(computation)
