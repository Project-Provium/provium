"""Durable queue scheduling on the reusable pipeline execution engine."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from provium_pipeline.models import PipelineConfiguration, PipelineDefinition
from provium_pipeline.publication import Publisher
from provium_pipeline.requests import InputSet
from provium_pipeline.runtime import ExecutionContext as BasicContext
from provium_pipeline.runtime import State
from provium_pipeline.store import Artifact, ArtifactCache

from .models import ComputeProfile, definition_snapshot
from .persistence.interface import QueueBackend
from .persistence.queues import validate_queue_name


class ExecutionContext(BasicContext):
    def __init__(
        self,
        configuration: PipelineConfiguration,
        store: ArtifactCache,
        *,
        definition: PipelineDefinition | None = None,
        procedures: dict[str, Any] | None = None,
        profile: ComputeProfile | None = None,
        only: list[str] | None = None,
        workers: list[dict[str, Any]] | None = None,
        force: list[str] | None = None,
        backend: QueueBackend | None = None,
        queue_group: str = "default",
        idle_wait: float = 0,
        log_path: str | Path | None = None,
        verify_artifacts: bool = False,
        strategy: str = "stage",
        targets: list[str] | None = None,
        publisher: Publisher | None = None,
    ) -> None:
        self.queue_group = validate_queue_name(queue_group)
        self.backend = backend
        if backend is not None:
            registered = backend.definition(configuration.name)
            if registered is not None:
                if (
                    definition is not None
                    and definition_snapshot(definition) != registered.snapshot()
                ):
                    raise ValueError("definition differs from registered snapshot")
                definition = registered
        if profile is not None and definition is not None:
            if profile.name not in getattr(definition, "profiles", frozenset[str]()):
                raise ValueError(f"profile not allowed: {profile.name}")
        if backend is not None and targets is not None:
            raise ValueError("target selection currently requires direct inputs")
        super().__init__(
            configuration,
            store,
            definition=definition,
            procedures=procedures,
            settings=profile,
            only=only,
            workers=workers,
            force=force,
            coordinator=backend,
            idle_wait=idle_wait,
            log_path=log_path,
            verify_artifacts=verify_artifacts,
            strategy=strategy,
            targets=targets,
            publisher=publisher,
        )
        self.queue_page_size = 100
        self._seen: set[int] = set()

    def _execute(self, items: Iterable[InputSet]) -> None:
        if self.backend is not None:
            with (
                self.store.writer_lock(shared=True),
                self.backend.reader(self.store.namespace),
            ):
                if self.store.root is not None:
                    (self.store.root / ".dispatch-cache").touch()
                self._run_queued()
        else:
            super()._execute(items)

    def _progress(
        self, state: State, status: str, outputs: Mapping[str, Artifact]
    ) -> None:
        if self.backend and state.identity is not None:
            self.backend.progress(
                state.identity,
                status,
                {
                    "outputs": {
                        name: artifact.location for name, artifact in outputs.items()
                    },
                    "store": self.store.namespace,
                    "run": self.run_id,
                    "log": str(self.log_path) if self.log_path is not None else None,
                    "error": state.error,
                },
            )

    def _load_page(self, rows: list[dict[str, Any]]) -> None:
        self._states.clear()
        for row in rows:
            identity = row["id"]
            self._seen.add(identity)
            if row["snapshot"] != self.plan.configuration.model_dump(mode="json"):
                raise ValueError("queued pipeline snapshot differs from execution")
            expected_definition = (
                self.plan.definition.snapshot() if self.plan.definition else None
            )
            if row.get("definition") != expected_definition:
                raise ValueError("queued definition differs from execution")
            from provium_pipeline.requests import ExecutionRequest

            request = (
                ExecutionRequest(row["inputs"], row["destinations"])
                if row.get("destinations") is not None
                else InputSet(row["inputs"])
            )
            self.publisher.validate([request])
            self._add(request, identity)

    def _run_queued_items(self) -> None:
        assert self.backend is not None
        after = 0
        idle_start = time.monotonic()
        while not self._stop.is_set():
            rows = self.backend.pending(
                self.plan.configuration.name,
                after,
                limit=self.queue_page_size,
                include_finished=bool(self.force),
                queue_group=self.queue_group,
            )
            if not rows:
                if (
                    not any(self._allowed(node) for node in self.plan.order)
                    or time.monotonic() - idle_start >= self.idle_wait
                ):
                    break
                self._stop.wait(min(0.1, self.idle_wait))
                continue
            for row in rows:
                if self._stop.is_set():
                    return
                after = row["id"]
                self._load_page([row])
                self._run_local()
            idle_start = time.monotonic()

    def _run_queued(self) -> None:
        if self.strategy == "item":
            self._run_queued_items()
            return
        assert self.backend is not None
        idle_start = time.monotonic()
        while not self._stop.is_set():
            before = len(self._step_seen)
            for node in self.plan.order or [None]:
                after = 0
                try:
                    while not self._stop.is_set():
                        rows = self.backend.pending(
                            self.plan.configuration.name,
                            after,
                            limit=self.queue_page_size,
                            include_finished=bool(self.force),
                            queue_group=self.queue_group,
                        )
                        if not rows:
                            break
                        after = rows[-1]["id"]
                        self._load_page(rows)
                        self._ready()
                        if node is not None:
                            jobs = self._ready(node)
                            while jobs and self._allowed(node):
                                completed = sum(
                                    len(state.steps) for state in self._states
                                )
                                self._run_node(jobs[0], keep_workers=True)
                                if completed == sum(
                                    len(state.steps) for state in self._states
                                ):
                                    break
                                jobs = self._ready(node)
                        self._ready()
                        for state in self._states:
                            self._record(state)
                finally:
                    self._unload_node()
                if len(self._step_seen) > before:
                    break
            if len(self._step_seen) > before:
                idle_start = time.monotonic()
                continue
            if not any(self._allowed(node) for node in self.plan.order):
                break
            if time.monotonic() - idle_start >= self.idle_wait:
                break
            time.sleep(min(0.1, self.idle_wait))
        after = 0
        while rows := self.backend.pending(
            self.plan.configuration.name,
            after,
            limit=self.queue_page_size,
            include_finished=True,
            queue_group=self.queue_group,
        ):
            after = rows[-1]["id"]
            remaining = [
                row
                for row in rows
                if row["id"] in self._seen and row["id"] not in self._pipeline_seen
            ]
            self._load_page(remaining)
            self._ready()
            for state in self._states:
                self._record(state, final=True)
