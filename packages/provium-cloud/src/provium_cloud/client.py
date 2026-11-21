"""Python interface for unnamed cloud invocations and their durable history."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Self

from provium_cloud.artifacts import ArtifactBackend, open_artifacts
from provium_cloud.deployment import CloudDeployment, SchedulingOptions, load_deployment
from provium_cloud.execution.interface import Executor
from provium_cloud.invocations.inputs import prepare_inputs
from provium_cloud.invocations.models import InvocationPlan, PipelineGraph
from provium_cloud.invocations.planning import prepare_pipeline, prepare_procedure
from provium_cloud.state import StateBackend, open_state
from provium_cloud.state.coordination import Coordinator
from provium_cloud.state.models import Computation, Run, RunPage
from provium_cloud.state.runs import RunStore


class CloudClient:
    def __init__(
        self,
        deployment: CloudDeployment,
        *,
        artifacts: ArtifactBackend | None = None,
        state: StateBackend | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.deployment = deployment
        self.artifacts = artifacts or open_artifacts(deployment.artifacts)
        self._owns_artifacts = artifacts is None
        try:
            self.state = state or open_state(
                deployment.state, namespace=self.artifacts.namespace
            )
        except BaseException:
            if self._owns_artifacts:
                self.artifacts.close()
            raise
        self.executor = executor
        self.runs = RunStore(self.state)
        self.coordinator = Coordinator(self.state)

    @classmethod
    def from_file(cls, path: str | Path) -> Self:
        return cls(load_deployment(path))

    def enqueue(
        self,
        plan: InvocationPlan,
        inputs: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> Run:
        values = prepare_inputs(plan, inputs, self.artifacts)
        return self.runs.create(plan, values, self.deployment, request_id=request_id)

    def enqueue_pipeline(
        self,
        graph: PipelineGraph | Mapping[str, Any] | str | Path,
        inputs: dict[str, Any],
        *,
        request_id: str | None = None,
        targets: Sequence[str] | None = None,
        force: bool | Sequence[str] = False,
        overrides: SchedulingOptions | Mapping[str, object] | None = None,
    ) -> Run:
        plan = prepare_pipeline(
            graph, self.deployment, targets=targets, force=force, overrides=overrides
        )
        return self.enqueue(plan, inputs, request_id=request_id)

    def enqueue_procedure(
        self,
        procedure: str,
        inputs: dict[str, Any],
        *,
        request_id: str | None = None,
        configuration: Mapping[str, Any] | None = None,
        tags: Sequence[str] = (),
        force: bool = False,
        overrides: SchedulingOptions | Mapping[str, object] | None = None,
    ) -> Run:
        plan = prepare_procedure(
            procedure,
            self.deployment,
            configuration=configuration,
            tags=tags,
            force=force,
            overrides=overrides,
        )
        return self.enqueue(plan, inputs, request_id=request_id)

    def get_run(self, identity: str) -> Run:
        return self.runs.refresh(identity)

    def list_runs(
        self,
        *,
        statuses: list[str] | None = None,
        input_set_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> RunPage:
        page = self.runs.list(
            statuses=statuses, input_set_id=input_set_id, cursor=cursor, limit=limit
        )
        page.items = [self.runs.refresh(run.id) for run in page.items]
        return page

    def cancel(self, identity: str) -> Run:
        run = self.runs.cancel(identity)
        for key in {task.computation for task in run.tasks.values()} - {None}:
            assert key is not None
            self.coordinator.cancel_orphans(key)
        return run

    def retry(self, identity: str) -> Run:
        return self.runs.retry(identity)

    def attempts(self, identity: str, node: str) -> Computation | None:
        task = self.get_run(identity).tasks[node]
        return (
            None if task.computation is None else self.coordinator.get(task.computation)
        )

    def wait(
        self, identity: str, *, timeout: float | None = None, interval: float = 2
    ) -> Run:
        if interval <= 0 or timeout is not None and timeout < 0:
            raise ValueError("invalid wait interval or timeout")
        start = monotonic()
        while True:
            run = self.get_run(identity)
            if run.status not in ("queued", "running"):
                return run
            if timeout is not None and monotonic() - start >= timeout:
                raise TimeoutError(f"timed out waiting for {identity}")
            sleep(interval)

    def fetch(
        self, identity: str, output: str, destination: str, *, overwrite: bool = False
    ) -> None:
        run = self.get_run(identity)
        if output not in run.outputs:
            raise LookupError(f"output is not available: {output}")
        self.artifacts.fetch(run.outputs[output], destination, overwrite=overwrite)

    def lookup(self, computation: str) -> dict[str, Any] | None:
        try:
            record = self.coordinator.get(computation)
        except LookupError:
            return None
        for generation in reversed(record.generations):
            if generation.status == "succeeded":
                outputs = generation.outputs or {}
                self.artifacts.verify(outputs, generation.payload["expected"])
                return outputs
        return None

    def close(self) -> None:
        if self._owns_artifacts:
            self.artifacts.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
