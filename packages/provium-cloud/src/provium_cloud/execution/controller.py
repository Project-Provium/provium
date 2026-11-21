"""Advance ready DAG nodes and reconcile durable work with compute providers."""

import logging
from threading import Event
from typing import Any

from provium.provenance import ProcedureRecord
from provium.semantic import computation_key, output_key

from provium_cloud.client import CloudClient
from provium_cloud.deployment import CloudDeployment
from provium_cloud.execution.aws.batch import BatchExecutor
from provium_cloud.execution.interface import Executor
from provium_cloud.state.jobs import JobStore
from provium_cloud.state.models import Run, Task
from provium_cloud.state.runs import input_identity, resolve_reference

logger = logging.getLogger(__name__)


def task_payload(run: Run, task: Task) -> tuple[str, dict[str, Any]]:
    bindings: dict[str, Any] = {}
    fingerprints: dict[str, dict[str, Any]] = {}
    for scope in ("inputs", "setup_inputs"):
        values: dict[str, Any] = {
            name: resolve_reference(run, ref)
            for name, ref in getattr(task.plan, scope).items()
        }
        for name, value in values.items():
            codec = task.plan.value_types.get(f"{scope}.{name}")
            if value is None and codec:
                values[name] = {"value_input": {"type": codec, "value": None}}
        bindings[scope] = values
        fingerprints[scope] = {
            name: input_identity(value) for name, value in values.items()
        }
    key = computation_key(
        ProcedureRecord.from_dict(task.plan.record),
        fingerprints["inputs"],
        fingerprints["setup_inputs"],
    )
    expected = {
        name: output_key(key, name, kind)
        for name, kind in task.plan.output_types.items()
    }
    return key, {
        "procedure": task.plan.procedure,
        "record": task.plan.record,
        "configuration": task.plan.configuration,
        "expected": expected,
        "deployment": run.deployment.model_dump(mode="json"),
        **bindings,
    }


class Controller:
    def __init__(
        self, client: CloudClient, *, executor: Executor | None = None
    ) -> None:
        self.client = client
        self.jobs = JobStore(client.state)
        self.executor = executor or client.executor
        self._providers: dict[str, Executor] = {}

    def provider(self, deployment: CloudDeployment) -> Executor:
        if self.executor is not None:
            return self.executor
        if deployment.region not in self._providers:
            self._providers[deployment.region] = BatchExecutor(deployment.region)
        return self._providers[deployment.region]

    def tick(self) -> None:
        cursor: str | None = None
        while True:
            page = self.client.runs.list(statuses=["queued", "running"], cursor=cursor)
            for snapshot in page.items:
                run = self.client.get_run(snapshot.id)
                if run.status not in ("queued", "running"):
                    continue
                for name in run.tasks:
                    run = self.client.get_run(run.id)
                    task = run.tasks[name]
                    if task.status != "waiting":
                        continue
                    try:
                        key, payload = task_payload(run, task)
                    except KeyError:
                        continue  # Upstream outputs are not committed yet.
                    try:
                        computation = self.client.coordinator.get(key)
                    except LookupError:
                        computation = None
                    if computation is not None:
                        for generation in computation.generations:
                            if generation.status == "succeeded":
                                try:
                                    self.client.artifacts.verify(
                                        generation.outputs or {},
                                        generation.payload["expected"],
                                    )
                                except (
                                    FileNotFoundError,
                                    ValueError,
                                    EOFError,
                                ) as error:
                                    self.jobs.invalidate(
                                        key, generation.number, str(error)
                                    )
                    self.client.coordinator.admit(run.id, name, key, payload)
                self.client.get_run(run.id)
            cursor = page.next_cursor
            if cursor is None:
                break
        for computation in self.jobs.active():
            self.client.coordinator.cancel_orphans(computation.key)
            for snapshot in computation.generations:
                generation = self.client.coordinator.get(computation.key).generations[
                    snapshot.number - 1
                ]
                deployment = CloudDeployment.model_validate(
                    generation.payload["deployment"]
                )
                provider = self.provider(deployment)
                if generation.status == "submitting" or generation.cancellation_pending:
                    found = provider.find(generation)
                    self.jobs.submitted(
                        computation.key,
                        generation.number,
                        generation.submit_owner or "",
                        found,
                    )
                    generation = self.client.coordinator.get(
                        computation.key
                    ).generations[generation.number - 1]
                statuses = provider.describe(generation.job_ids)
                self.jobs.observed(computation.key, generation.number, statuses)
                generation = self.client.coordinator.get(computation.key).generations[
                    generation.number - 1
                ]
                if generation.cancellation_pending:
                    for job, status in statuses.items():
                        provider.cancel(
                            job,
                            status,
                            generation.error or "superseded by a newer invocation",
                        )
                    continue
                owner = self.jobs.reserve(computation.key, generation.number)
                if owner is None:
                    continue
                try:
                    job = provider.submit(computation.key, generation, deployment)
                except Exception as error:
                    self.jobs.submission_error(
                        computation.key, generation.number, str(error)
                    )
                    logger.warning("submission pending reconciliation: %s", error)
                else:
                    self.jobs.submitted(
                        computation.key, generation.number, owner, [job]
                    )

    def serve(self, *, interval: float = 2, stop: Event | None = None) -> None:
        if interval <= 0:
            raise ValueError("controller interval must be positive")
        stop = stop or Event()
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("controller tick failed; durable work will be retried")
            stop.wait(interval)
