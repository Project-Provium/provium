"""Invocation history and task progress, independent of execution providers."""

from typing import Any, cast
from uuid import uuid4

from provium.semantic import canonical_hash

from provium_cloud.deployment import CloudDeployment
from provium_cloud.invocations.models import InvocationPlan
from provium_cloud.state.interface import StateBackend, StateTransaction
from provium_cloud.state.models import Run, RunPage, Task

ACTIVE_RUNS = ["queued", "running"]


def require_run(tx: StateTransaction, identity: str) -> Run:
    run = tx.get_run(identity)
    if run is None:
        raise LookupError(f"unknown run: {identity}")
    return run


def input_identity(value: Any) -> Any:
    if isinstance(value, dict) and "fingerprint" in value:
        return cast(dict[str, Any], value)["fingerprint"]
    return cast(Any, value)


class RunStore:
    def __init__(self, backend: StateBackend) -> None:
        self.backend = backend

    def create(
        self,
        plan: InvocationPlan,
        inputs: dict[str, Any],
        deployment: CloudDeployment,
        *,
        request_id: str | None = None,
    ) -> Run:
        identity = {name: input_identity(value) for name, value in inputs.items()}
        digest = canonical_hash(
            {
                "plan": plan.model_dump(mode="json"),
                "inputs": identity,
                "deployment": deployment.model_dump(mode="json"),
            }
        )
        with self.backend.transaction() as tx:
            if request_id is not None:
                if not request_id.strip():
                    raise ValueError("request ID cannot be blank")
                previous = tx.request(request_id)
                if previous is not None:
                    if previous[1] != digest:
                        raise ValueError(
                            "request ID already used with different parameters"
                        )
                    return require_run(tx, previous[0])
            run = Run(
                id=f"run_{int(tx.now * 1_000_000):020d}_{uuid4().hex}",
                input_set_id=canonical_hash(identity),
                inputs=inputs,
                targets=plan.targets,
                tasks={name: Task(plan=task) for name, task in plan.tasks.items()},
                deployment=deployment,
                created_at=tx.now,
                updated_at=tx.now,
            )
            tx.save_run(run)
            if request_id is not None:
                tx.save_request(request_id, run.id, digest)
            return run

    def get(self, identity: str) -> Run:
        with self.backend.transaction() as tx:
            return require_run(tx, identity)

    def list(
        self,
        *,
        statuses: list[str] | None = None,
        input_set_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> RunPage:
        if not 1 <= limit <= 999:
            raise ValueError("limit must be between 1 and 999")
        with self.backend.transaction() as tx:
            runs = tx.runs(
                statuses=statuses,
                input_set_id=input_set_id,
                after=cursor or "",
                limit=limit + 1,
            )
        return RunPage(
            items=runs[:limit],
            next_cursor=runs[limit - 1].id if len(runs) > limit else None,
        )

    def refresh(self, identity: str) -> Run:
        with self.backend.transaction() as tx:
            run = require_run(tx, identity)
            if run.status not in ACTIVE_RUNS:
                return run
            for task in run.tasks.values():
                if (
                    task.status in ("completed", "failed", "blocked", "canceled")
                    or task.computation is None
                ):
                    continue
                computation = tx.get_computation(task.computation)
                assert computation is not None and task.generation is not None
                generation = computation.generations[task.generation - 1]
                while generation.status == "superseded":
                    generation = computation.generations[generation.number]
                    task.generation = generation.number
                if generation.status == "succeeded":
                    task.status = "completed"
                    task.outputs = generation.outputs or {}
                elif generation.status in ("failed", "canceled"):
                    task.status = "failed"
                    task.error = generation.error or generation.status
                else:
                    task.status = (
                        "running" if generation.status == "running" else "queued"
                    )
            if any(task.status == "failed" for task in run.tasks.values()):
                run.status = "failed"
                run.error = next(
                    task.error for task in run.tasks.values() if task.status == "failed"
                )
                for task in run.tasks.values():
                    if task.status not in ("completed", "failed"):
                        task.status = "blocked"
            elif all(task.status == "completed" for task in run.tasks.values()):
                run.status = "succeeded"
                run.outputs = {
                    name: resolve_reference(run, ref)
                    for name, ref in run.targets.items()
                }
            elif any(task.status != "waiting" for task in run.tasks.values()):
                run.status = "running"
            run.updated_at = tx.now
            tx.save_run(run)
            return run

    def cancel(self, identity: str) -> Run:
        with self.backend.transaction() as tx:
            run = require_run(tx, identity)
            if run.status in ACTIVE_RUNS:
                run.status = "canceled"
                for task in run.tasks.values():
                    if task.status != "completed":
                        task.status = "canceled"
                run.updated_at = tx.now
                tx.save_run(run)
            return run

    def retry(self, identity: str) -> Run:
        with self.backend.transaction() as tx:
            run = require_run(tx, identity)
            if run.status not in ("failed", "canceled"):
                raise ValueError("only failed or canceled runs can be retried")
            for name, task in run.tasks.items():
                if task.status != "completed":
                    run.tasks[name] = Task(plan=task.plan)
            run.status, run.error, run.updated_at = "queued", None, tx.now
            tx.save_run(run)
            return run


def resolve_reference(run: Run, reference: str | None) -> Any:
    if reference is None:
        return None
    if reference.startswith("$inputs."):
        return run.inputs[reference.removeprefix("$inputs.")]
    _, node, output = reference.split(".", 2)
    return run.tasks[node].outputs[output]
