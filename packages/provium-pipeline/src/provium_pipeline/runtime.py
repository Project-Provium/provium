"""Asynchronous context coordinating reusable process workers and callbacks."""

from __future__ import annotations

import logging
import math
import queue
import tempfile
import threading
import time
import traceback
from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from uuid import uuid4

from provium.semantic import canonical_hash, computation_key, output_key

from .database import Lease, QueueBackend
from .models import (
    ComputeProfile,
    PipelineConfiguration,
    PipelineDefinition,
    Settings,
)
from .planning import build_plan
from .store import Artifact, ArtifactStore
from .workers import Worker


@dataclass(frozen=True)
class InputSet:
    inputs: Mapping[str, str | Artifact | dict[str, Any]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "inputs", MappingProxyType(dict(self.inputs)))


@dataclass(frozen=True)
class Outcome:
    input_set: InputSet
    state: str
    outputs: Mapping[str, Artifact] = field(default_factory=dict[str, Artifact])
    node: str | None = None
    reused: bool = False
    error: str | None = None


@dataclass
class State:
    item: InputSet
    identity: int | None = None
    values: dict[str, Artifact] = field(default_factory=dict[str, Artifact])
    steps: set[str] = field(default_factory=set[str])
    failed: bool = False


@dataclass
class Job:
    state: State
    node: str
    key: str
    expected: dict[str, str]
    inputs: dict[str, Any]
    setup: dict[str, Any]
    setup_key: str
    lease: Lease | None = None
    directory: str = ""
    interrupted: bool = False
    accepted: bool = False


class ExecutionContext:
    def __init__(
        self,
        configuration: PipelineConfiguration,
        store: ArtifactStore,
        *,
        definition: PipelineDefinition | None = None,
        procedures: dict[str, Any] | None = None,
        profile: ComputeProfile | None = None,
        only: list[str] | None = None,
        workers: list[dict[str, Any]] | None = None,
        force: list[str] | None = None,
        backend: QueueBackend | None = None,
        idle_wait: float = 0,
        log_path: str | Path | None = None,
    ) -> None:
        if backend:
            registered_definition = backend.definition(configuration.name)
            if registered_definition:
                if (
                    definition
                    and definition.snapshot() != registered_definition.snapshot()
                ):
                    raise ValueError("definition differs from registered snapshot")
                definition = registered_definition
        self.plan = build_plan(configuration, definition, procedures)
        self.store = store
        self.profile = profile or ComputeProfile(name="default")
        if (
            profile is not None
            and definition is not None
            and profile.name not in definition.profiles
        ):
            raise ValueError(f"profile not allowed: {profile.name}")
        self.only = only or []
        self.force = force or []
        self.overrides = workers or []
        self.backend = backend
        self.idle_wait = idle_wait
        if not math.isfinite(idle_wait) or idle_wait < 0:
            raise ValueError("idle wait must be finite and nonnegative")
        self.run_id = uuid4().hex
        self.error: BaseException | None = None
        self.outcomes: list[Outcome] = []
        self.worker_starts = 0
        self.queue_page_size = 100
        self.max_resident_inputs = 0
        self._step_seen: set[tuple[int, str]] = set()
        self._pipeline_seen: set[int] = set()
        self._resident_setup: str | None = None
        self._finished = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._notifications: queue.Queue[Any] = queue.Queue(maxsize=1024)
        self._workers: list[Worker] = []
        self._lock = threading.RLock()
        self._leases: dict[str, Lease] = {}
        self._failed: dict[str, str] = {}
        self._forced: set[str] = set()
        self._seen: set[int] = set()
        self._states: list[State] = []
        self._active: str | None = None
        self._active_start = 0.0
        self._elapsed: dict[str, float] = {}
        self._started = 0.0
        self._settings = self._validate_settings()
        self.log_path = Path(
            log_path or Path(tempfile.gettempdir()) / f"provium-{self.run_id}.log"
        )
        self.logger = logging.getLogger(f"provium-pipeline.{self.run_id}")
        self.logger.setLevel(logging.INFO)
        self._handler = logging.FileHandler(self.log_path)
        self.logger.addHandler(self._handler)
        self.logger.propagate = False

    def _matches(self, node: str, selectors: list[str]) -> bool:
        tags = self.plan.configuration.nodes[node].tags
        return any(
            selector == "all"
            or selector == f"nodes.{node}"
            or selector.startswith("tags.")
            and selector[5:] in tags
            for selector in selectors
        )

    def _validate_settings(self) -> dict[str, Settings]:
        graph = self.plan.configuration
        for selector in self.only + self.force:
            if selector == "all" and selector in self.force:
                continue
            if not selector.startswith(("nodes.", "tags.")) or not any(
                self._matches(node, [selector]) for node in graph.nodes
            ):
                raise ValueError(f"unknown or unmatched selector: {selector}")
        rules: list[dict[str, Any]] = []
        for rule in self.overrides:
            if len(rule) != 1:
                raise ValueError("worker override needs one selector")
            selector, value = next(iter(rule.items()))
            if selector.startswith("nodes."):
                if selector[6:] not in graph.nodes:
                    raise ValueError(f"unknown node: {selector}")
                selector = f"pipelines.{graph.name}.{selector}"
            rules.append({selector: value})
        ComputeProfile(name="overrides", workers=rules)
        for rule in (
            self.profile.workers
            + self.profile.soft_time_limits
            + self.profile.hard_time_limits
            + rules
        ):
            selector = next(iter(rule))
            prefix = f"pipelines.{graph.name}."
            applicable = (
                selector[len(prefix) :] if selector.startswith(prefix) else selector
            )
            if applicable.startswith(("tags.", "nodes.")) and not any(
                self._matches(n, [applicable]) for n in graph.nodes
            ):
                raise ValueError(f"unknown or unmatched selector: {selector}")
        return {
            name: self.profile.settings(graph.name, name, node.tags, rules)
            for name, node in graph.nodes.items()
        }

    @property
    def settings(self) -> Mapping[str, Settings]:
        return dict(self._settings)

    @property
    def finished(self) -> bool:
        return self._finished.is_set()

    def start(
        self,
        input_sets: Iterable[InputSet] = (),
        *,
        on_step_finished: Callable[[Outcome], None] | None = None,
        on_pipeline_finished: Callable[[Outcome], None] | None = None,
    ) -> ExecutionContext:
        if self._closed:
            raise RuntimeError("context is closed")
        if self._thread is not None:
            raise RuntimeError("context can only start once")
        self._on_step = on_step_finished
        self._on_pipeline = on_pipeline_finished
        self._thread = threading.Thread(
            target=self._run, args=(input_sets,), name="provium-coordinator"
        )
        self._thread.start()
        return self

    def __enter__(self) -> ExecutionContext:
        return self

    def __exit__(self, *args: Any) -> None:
        if args and args[0] is not None:
            self._stop.set()
        self.close()

    def close(self) -> None:
        self._closed = True
        if self._thread:
            self._thread.join()
        self._handler.close()
        self.logger.removeHandler(self._handler)
        if self.error is not None:
            raise RuntimeError(
                f"pipeline executor failed; see {self.log_path}"
            ) from self.error

    def inspect_inputs(self, items: list[InputSet]) -> list[dict[str, Any]]:
        if self._thread is not None or self._closed:
            raise RuntimeError("inspection requires an unused, open context")
        reports: list[dict[str, Any]] = []
        for item in items:
            self._add(item)
            state = self._states[-1]
            steps: dict[str, Any] = {}
            for node in self.plan.order:
                job = self._job(state, node)
                if job is None:
                    steps[node] = {"state": "blocked"}
                    continue
                try:
                    manifest = (
                        self.backend.result(self.store.namespace, job.key)
                        if self.backend
                        else self.store.manifest(job.key)
                    )
                    if manifest is None:
                        steps[node] = {"state": "missing", "provenance": job.expected}
                        continue
                    outputs = self.store.verify(manifest, job.expected)
                    state.values.update(
                        {
                            f"$nodes.{node}.{name}": artifact
                            for name, artifact in outputs.items()
                        }
                    )
                    steps[node] = {
                        "state": "completed",
                        "outputs": {
                            name: {
                                "location": artifact.location,
                                **artifact.fingerprint,
                            }
                            for name, artifact in outputs.items()
                        },
                    }
                except Exception as error:
                    steps[node] = {"state": "invalid", "error": str(error)}
            outputs = {
                name: state.values[ref]
                for name, ref in self.plan.configuration.outputs.items()
                if ref in state.values
            }
            report = _outcome_json(
                Outcome(
                    item,
                    "failed"
                    if state.failed
                    else "completed"
                    if len(outputs) == len(self.plan.configuration.outputs)
                    else "partial",
                    outputs,
                )
            )
            report["nodes"] = {
                name: detail
                for name, detail in steps.items()
                if not self.only or self._matches(name, self.only)
            }
            reports.append(report)
        self._states.clear()
        return reports

    def cancel(self) -> None:
        self._stop.set()

    def _notify(self, event: Outcome) -> None:
        try:
            self._notifications.put_nowait(event)
        except queue.Full:
            self.logger.exception("callback queue overflow: %s", event.node)

    def _dispatch(self) -> None:
        while True:
            event = self._notifications.get()
            if event is None:
                return
            callback = self._on_step if event.node is not None else self._on_pipeline
            if callback:
                try:
                    callback(event)
                except Exception:
                    self.logger.exception("callback failed")

    def _monitor(self) -> None:
        while not self._stop_monitor.wait(0.02):
            now = time.monotonic()
            global_hard = self.profile.hard_time_limit
            node = self._active
            node_hard = self._settings[node].hard if node else None
            expired = (
                global_hard is not None
                and now - self._started >= global_hard
                or node_hard is not None
                and self._active_time(node) >= node_hard
            )
            if expired:
                with self._lock:
                    for worker in self._workers:
                        if worker.job and worker.job.accepted:
                            continue
                        if worker.job:
                            worker.job.interrupted = True
                        if worker.process.is_alive():
                            worker.process.terminate()

    def _heartbeat(self) -> None:
        while not self._stop_monitor.wait(5):
            if self.backend is None:
                continue
            with self._lock:
                leases = list(self._leases.values())
            for lease in leases:
                try:
                    valid = self.backend.renew(lease)
                except Exception:
                    self.logger.exception("lease renewal failed: %s", lease.key)
                    valid = False
                if not valid:
                    with self._lock:
                        for worker in self._workers:
                            if worker.job and worker.job.key == lease.key:
                                worker.job.interrupted = True
                                if worker.process.is_alive():
                                    worker.process.terminate()

    def _active_time(self, node: str | None) -> float:
        if node is None:
            return 0
        return self._elapsed.get(node, 0) + (
            time.monotonic() - self._active_start if node == self._active else 0
        )

    def _allowed(self, node: str) -> bool:
        if self._stop.is_set() or self.only and not self._matches(node, self.only):
            return False
        elapsed = time.monotonic() - self._started
        for limit in (self.profile.soft_time_limit, self.profile.hard_time_limit):
            if limit is not None and elapsed >= limit:
                return False
        for limit in (self._settings[node].soft, self._settings[node].hard):
            if limit is not None and self._active_time(node) >= limit:
                return False
        return True

    def _add(self, item: InputSet, identity: int | None = None) -> None:
        state = State(item, identity)
        self._states.append(state)
        self.max_resident_inputs = max(self.max_resident_inputs, len(self._states))
        try:
            expected_names = set(self.plan.configuration.inputs)
            if set(item.inputs) != expected_names:
                raise ValueError("input names do not match pipeline definition")  # noqa: TRY301 -- converted to an item outcome
            for name, value in item.inputs.items():
                if isinstance(value, Artifact):
                    artifact = self.store.inspect(value.location)
                    if (
                        artifact.fingerprint != value.fingerprint
                        or artifact.identity != value.identity
                    ):
                        raise ValueError("input artifact changed")  # noqa: TRY301 -- converted to an item outcome
                elif isinstance(value, dict):
                    artifact = self.store.inspect(value["location"])
                    if (
                        artifact.fingerprint != value["fingerprint"]
                        or artifact.identity != value["identity"]
                    ):
                        raise ValueError("queued input artifact changed")  # noqa: TRY301 -- converted to an item outcome
                else:
                    artifact = self.store.inspect(value)
                expected = self.plan.input_types.get(name)
                if expected is not None and artifact.fingerprint["type"] != expected:
                    raise ValueError(f"incompatible pipeline input: {name}")  # noqa: TRY301 -- converted to an item outcome
                state.values[f"$inputs.{name}"] = artifact
        except Exception:
            state.failed = True
            self.logger.exception("invalid input set %s", identity)

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
            self._add(InputSet(row["inputs"]), identity)

    def _record(self, state: State, *, final: bool = False) -> None:
        outputs = {
            name: state.values[ref]
            for name, ref in self.plan.configuration.outputs.items()
            if ref in state.values
        }
        status = (
            "failed"
            if state.failed
            else "completed"
            if len(outputs) == len(self.plan.configuration.outputs)
            else "cancelled"
            if self._stop.is_set()
            else "partial"
        )
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
                    "log": str(self.log_path),
                },
            )
        identity = state.identity if state.identity is not None else id(state)
        if identity not in self._pipeline_seen and (
            final or status in ("completed", "failed")
        ):
            self._pipeline_seen.add(identity)
            event = Outcome(state.item, status, outputs)
            self.outcomes.append(event)
            self._notify(event)

    def _run_queued(self) -> None:
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

    def _job(self, state: State, name: str) -> Job | None:
        if state.failed or name in state.steps:
            return None
        node = self.plan.configuration.nodes[name]

        def bind(values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            paths: dict[str, Any] = {}
            fingerprints: dict[str, Any] = {}
            for field_name, value in values.items():
                if value is None:
                    paths[field_name], fingerprints[field_name] = None, None
                elif isinstance(value, list):
                    paths[field_name] = [
                        state.values[ref].path for ref in cast(list[str], value)
                    ]
                    fingerprints[field_name] = [
                        state.values[ref].fingerprint for ref in cast(list[str], value)
                    ]
                else:
                    paths[field_name] = state.values[value].path
                    fingerprints[field_name] = state.values[value].fingerprint
            return paths, fingerprints

        try:
            inputs, input_fingerprints = bind(node.inputs)
            setup, setup_fingerprints = bind(node.setup_inputs)
        except KeyError:
            return None
        # Core records optional absences even when omitted in graph YAML.
        contract = self.plan.procedures[name].resolve_contract()
        for scope, paths, fingerprints in (
            ("inputs", inputs, input_fingerprints),
            ("setup_inputs", setup, setup_fingerprints),
        ):
            for field_info in getattr(contract.metadata, scope):
                if field_info.name not in paths:
                    paths[field_info.name] = [] if field_info.repeated else None
                    fingerprints[field_info.name] = [] if field_info.repeated else None
        key = computation_key(
            self.plan.records[name], input_fingerprints, setup_fingerprints
        )
        expected = {
            output: output_key(key, output, artifact_type)
            for output, artifact_type in self.plan.output_types[name].items()
        }
        return Job(
            state,
            name,
            key,
            expected,
            inputs,
            setup,
            canonical_hash(setup_fingerprints),
        )

    def _accept(self, job: Job, outputs: dict[str, Artifact], reused: bool) -> None:
        job.state.values.update(
            {
                f"$nodes.{job.node}.{name}": artifact
                for name, artifact in outputs.items()
            }
        )
        job.state.steps.add(job.node)
        notification = (
            job.state.identity if job.state.identity is not None else id(job.state),
            job.node,
        )
        if notification not in self._step_seen:
            self._step_seen.add(notification)
            self._notify(
                Outcome(job.state.item, "completed", outputs, job.node, reused)
            )

    def _failure(self, job: Job, error: str) -> None:
        self._failed[job.key] = error
        job.state.failed = True
        job.state.steps.add(job.node)
        self.logger.error(
            "node=%s computation=%s input=%s: %s",
            job.node,
            job.key,
            job.state.identity,
            error,
        )
        if self.backend and job.lease:
            self.backend.fail(job.lease, error)
        notification = (
            job.state.identity if job.state.identity is not None else id(job.state),
            job.node,
        )
        if notification not in self._step_seen:
            self._step_seen.add(notification)
            self._notify(Outcome(job.state.item, "failed", node=job.node, error=error))

    def _ready(self, node: str | None = None) -> list[Job]:
        ready: list[Job] = []
        for name in [node] if node else self.plan.order:
            for state in self._states:
                job = self._job(state, name)
                if job is None:
                    continue
                if job.key in self._failed:
                    self._failure(job, self._failed[job.key])
                    continue
                forced = (
                    self._allowed(name)
                    and self._matches(name, self.force)
                    and job.key not in self._forced
                )
                try:
                    manifest = (
                        self.backend.result(self.store.namespace, job.key)
                        if self.backend
                        else self.store.manifest(job.key)
                    )
                    if manifest is not None and not forced:
                        self._accept(
                            job, self.store.verify(manifest, job.expected), True
                        )
                    elif self._allowed(name):
                        ready.append(job)
                except Exception:
                    self._failure(job, traceback.format_exc())
        return ready

    def _run_node(self, first: Job, *, keep_workers: bool = False) -> None:
        name = first.node
        settings = self._settings[name]
        if self._active != name or self._resident_setup != first.setup_key:
            self._unload_node()
            self._active = name
            self._active_start = time.monotonic()
            self._resident_setup = first.setup_key
        try:
            while True:
                candidates = self._ready(name)
                busy_keys = {worker.job.key for worker in self._workers if worker.job}
                candidates = [
                    job
                    for job in candidates
                    if job.key not in busy_keys and job.setup_key == first.setup_key
                ]
                for job in candidates:
                    if job.key in busy_keys:
                        continue
                    if not self._allowed(name):
                        break
                    idle = next(
                        (
                            worker
                            for worker in self._workers
                            if worker.ready and worker.job is None
                        ),
                        None,
                    )
                    if idle is None and len(self._workers) >= settings.workers:
                        break
                    if self.backend:
                        job.lease = self.backend.acquire(
                            self.store.namespace,
                            job.key,
                            self.run_id,
                            input_id=job.state.identity,
                            force=self._matches(name, self.force)
                            and job.key not in self._forced,
                        )
                        if job.lease is None:
                            self.logger.info(
                                "lease contention pipeline=%s node=%s computation=%s",
                                self.plan.configuration.name,
                                name,
                                job.key,
                            )
                            continue
                        with self._lock:
                            self._leases[job.key] = job.lease
                    job.directory = tempfile.mkdtemp(prefix="job-", dir=self._scratch)
                    if idle is None:
                        idle = Worker.start(
                            self.plan.procedures[name],
                            self.plan.configurations[name],
                            job.setup,
                        )
                        self.worker_starts += 1
                        with self._lock:
                            self._workers.append(idle)
                    idle.job = job
                    if idle.ready:
                        self._send(idle)
                    busy_keys.add(job.key)
                progress = False
                for worker in list(self._workers):
                    if worker.connection.poll():
                        try:
                            kind, value = worker.connection.recv()
                        except (EOFError, OSError):
                            kind, value = "error", "worker exited without a result"
                        if kind == "ready":
                            worker.ready = True
                            if worker.job:
                                self._send(worker)
                        elif worker.job:
                            job = worker.job
                            try:
                                if kind == "error" or job.interrupted:
                                    self._failure(
                                        job,
                                        str(value)
                                        if not job.interrupted
                                        else "hard time limit or lost lease",
                                    )
                                else:
                                    outputs = self.store.stage(
                                        job.key,
                                        cast(dict[str, str], value),
                                        job.expected,
                                    )
                                    with self._lock:
                                        if job.interrupted:
                                            raise RuntimeError(  # noqa: TRY301
                                                "publication interrupted"
                                            )
                                        job.accepted = True
                                    self._publish(job, outputs)
                                    self._forced.add(job.key)
                                    self._accept(
                                        job,
                                        self.store.verify(outputs, job.expected),
                                        False,
                                    )
                            except Exception:
                                self._failure(job, traceback.format_exc())
                            finally:
                                with self._lock:
                                    self._leases.pop(job.key, None)
                                worker.job = None
                            progress = True
                    if not worker.process.is_alive():
                        if worker.job:
                            job = worker.job
                            self._failure(
                                job, "hard time limit or worker process exited"
                            )
                            with self._lock:
                                self._leases.pop(job.key, None)
                            worker.job = None
                        with self._lock:
                            self._workers.remove(worker)
                        worker.close(terminate=True)
                        progress = True
                if not any(worker.job for worker in self._workers):
                    remaining = [
                        job
                        for job in self._ready(name)
                        if job.setup_key == first.setup_key
                    ]
                    if not remaining or not self._allowed(name):
                        break
                    if not progress and self.backend:
                        break
                time.sleep(0.01)
        finally:
            if not keep_workers:
                self._unload_node()

    def _publish(self, job: Job, outputs: dict[str, Any]) -> None:
        try:
            if self.backend and job.lease:
                committed = self.backend.complete(job.lease, outputs)
            else:
                self.store.commit(job.key, outputs)
                committed = True
        except Exception:
            # Publication is idempotently reconciled; the procedure is never retried.
            observed = (
                self.backend.result(self.store.namespace, job.key)
                if self.backend
                else self.store.manifest(job.key)
            )
            if observed != outputs:
                raise
            committed = True
            self.logger.info("recovered publication acknowledgement: %s", job.key)
        if not committed:
            raise RuntimeError("publication lease lost")

    def _unload_node(self) -> None:
        with self._lock:
            for worker in self._workers:
                worker.close(terminate=worker.job is not None)
            self._workers.clear()
        if self._active is not None:
            self._elapsed[self._active] = self._active_time(self._active)
        self._active = None
        self._resident_setup = None

    def _send(self, worker: Worker) -> None:
        job = worker.job
        paths = {
            name: str(Path(job.directory) / f"{index}.pa")
            for index, name in enumerate(job.expected)
        }
        try:
            worker.connection.send((job.inputs, paths))
        except OSError:
            self.logger.exception("worker dispatch failed: %s", job.key)
            job.interrupted = True
            if worker.process.is_alive():
                worker.process.terminate()

    def _run(self, items: Iterable[InputSet]) -> None:
        self._started = time.monotonic()
        self._stop_monitor = threading.Event()
        dispatcher = threading.Thread(target=self._dispatch, daemon=True)
        monitor = threading.Thread(target=self._monitor, daemon=True)
        heartbeat = threading.Thread(target=self._heartbeat, daemon=True)
        dispatcher.start()
        monitor.start()
        heartbeat.start()
        try:
            with ExitStack() as stack:
                if self.backend is None:
                    stack.enter_context(self.store.writer_lock())
                self._run_body(items)
        except BaseException as error:
            self.error = error
            self.logger.exception("executor failed")
        finally:
            self._stop_monitor.set()
            monitor.join()
            heartbeat.join(timeout=2)
            try:
                self._notifications.put(None, timeout=2)
            except queue.Full:
                self.logger.exception("callback queue did not drain")
            dispatcher.join(timeout=2)
            if dispatcher.is_alive():
                self.logger.error(
                    "callback shutdown grace expired; callbacks may remain undelivered"
                )
            self.logger.info(
                "run=%s elapsed=%s node_active=%s worker_starts=%s",
                self.run_id,
                time.monotonic() - self._started,
                self._elapsed,
                self.worker_starts,
            )
            self._finished.set()

    def _run_body(self, items: Iterable[InputSet]) -> None:
        with tempfile.TemporaryDirectory(
            prefix="provium-run-", dir=self.store.scratch
        ) as scratch:
            self._scratch = scratch
            if self.backend:
                self._run_queued()
                return
            for item in items:
                self._add(item)
            idle_start = time.monotonic()
            while not self._stop.is_set():
                ready = self._ready()
                if ready:
                    before = sum(len(state.steps) for state in self._states)
                    self._run_node(ready[0])
                    after = sum(len(state.steps) for state in self._states)
                    if after > before:
                        idle_start = time.monotonic()
                        continue
                if not any(self._allowed(node) for node in self.plan.order):
                    break
                if time.monotonic() - idle_start >= self.idle_wait:
                    break
                time.sleep(min(0.1, self.idle_wait))
            for state in self._states:
                self._record(state, final=True)


def _outcome_json(event: Outcome) -> dict[str, Any]:
    return {
        "state": event.state,
        "node": event.node,
        "reused": event.reused,
        "outputs": {
            name: {"location": artifact.location, **artifact.fingerprint}
            for name, artifact in event.outputs.items()
        },
        "error": event.error,
    }
