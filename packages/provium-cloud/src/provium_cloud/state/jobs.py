"""Durable submission reservations and reconciliation of provider observations."""

from uuid import uuid4

from provium_cloud.state.coordination import (
    ACTIVE,
    require_computation,
    require_generation,
)
from provium_cloud.state.interface import StateBackend
from provium_cloud.state.models import Computation


class JobStore:
    def __init__(self, backend: StateBackend) -> None:
        self.backend = backend

    def active(self) -> list[Computation]:
        result: list[Computation] = []
        cursor = ""
        while True:
            with self.backend.transaction() as tx:
                page = tx.computations(after=cursor)
            if not page:
                return result
            result.extend(page)
            cursor = page[-1].key

    def reserve(self, key: str, number: int, *, ttl: float = 120) -> str | None:
        with self.backend.transaction() as tx:
            computation = require_computation(tx, key)
            generation = require_generation(computation, number)
            if (
                generation.status not in ("queued", "submitting")
                or generation.submit_until > tx.now
            ):
                return None
            if any(
                g.status == "running" or g.cancellation_pending
                for g in computation.generations
                if g.number != number
            ):
                return None
            generation.status = "submitting"
            generation.submit_owner = uuid4().hex
            generation.submit_until = tx.now + ttl
            generation.updated_at = tx.now
            tx.save_computation(computation)
            return generation.submit_owner

    def submitted(self, key: str, number: int, owner: str, jobs: list[str]) -> None:
        with self.backend.transaction() as tx:
            computation = require_computation(tx, key)
            generation = require_generation(computation, number)
            generation.job_ids = list(dict.fromkeys([*generation.job_ids, *jobs]))
            if (
                jobs
                and generation.status == "submitting"
                and generation.submit_owner == owner
            ):
                generation.status = "submitted"
            elif generation.status in ("superseded", "canceled", "failed") and jobs:
                generation.cancellation_pending = True
            generation.updated_at = tx.now
            tx.save_computation(computation)

    def submission_error(self, key: str, number: int, error: str) -> None:
        # Keep the reservation: an interrupted response does not prove rejection.
        with self.backend.transaction() as tx:
            computation = require_computation(tx, key)
            computation.generations[number - 1].error = error
            tx.save_computation(computation)

    def observed(self, key: str, number: int, statuses: dict[str, str]) -> None:
        with self.backend.transaction() as tx:
            computation = require_computation(tx, key)
            generation = require_generation(computation, number)
            generation.job_ids = list(dict.fromkeys([*generation.job_ids, *statuses]))
            terminal = bool(generation.job_ids) and all(
                statuses.get(job) in ("SUCCEEDED", "FAILED")
                for job in generation.job_ids
            )
            if generation.status == "running" and generation.lease_until <= tx.now:
                generation.status, generation.error = "failed", "worker lease expired"
                generation.cancellation_pending = bool(generation.job_ids)
            if terminal:
                generation.cancellation_pending = False
                if generation.status in ACTIVE:
                    generation.status, generation.error = (
                        "failed",
                        "provider job ended without publishing outputs",
                    )
            elif (
                generation.cancellation_pending
                and not generation.job_ids
                and generation.submit_until <= tx.now
            ):
                # An unacknowledged, late submission cannot claim a retired generation.
                generation.cancellation_pending = False
            generation.updated_at = tx.now
            tx.save_computation(computation)

    def invalidate(self, key: str, number: int, error: str) -> None:
        with self.backend.transaction() as tx:
            computation = require_computation(tx, key)
            generation = require_generation(computation, number)
            if generation.status == "succeeded":
                generation.status, generation.error = "failed", error
                tx.save_computation(computation)
