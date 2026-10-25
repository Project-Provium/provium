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
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from provium.semantic import canonical_hash, computation_key, output_key

from provium_pipeline.coordination import Coordinator, Lease
from provium_pipeline.models import (
    ExecutionSettings,
    PipelineConfiguration,
    PipelineDefinition,
    Settings,
)
from provium_pipeline.planning import (
    Plan,
    build_plan,
    matches,
    resolve_settings,
    select_targets,
)
from provium_pipeline.publication import FilePublisher, Publisher
from provium_pipeline.requests import InputSet
from provium_pipeline.store import Artifact, ArtifactCache

if TYPE_CHECKING:
    from provium_pipeline.workers import Worker


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
    plan: Plan | None = None
    published: dict[str, Artifact] = field(default_factory=dict[str, Artifact])
    values: dict[str, Artifact] = field(default_factory=dict[str, Artifact])
    steps: set[str] = field(default_factory=set[str])
    failed: bool = False
    error: str | None = None


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
        store: ArtifactCache,
        *,
        definition: PipelineDefinition | None = None,
        procedures: dict[str, Any] | None = None,
        settings: ExecutionSettings | None = None,
        only: list[str] | None = None,
        workers: list[dict[str, Any]] | None = None,
        force: list[str] | None = None,
        coordinator: Coordinator | None = None,
        idle_wait: float = 0,
        log_path: str | Path | None = None,
        verify_artifacts: bool = False,
        strategy: str = "stage",
        targets: list[str] | None = None,
        publisher: Publisher | None = None,
    ) -> None:
        if strategy not in {"item", "stage"}:
            raise ValueError("strategy must be item or stage")
        self.strategy = strategy
        self.plan = build_plan(configuration, definition, procedures)
        if targets is not None:
            self.plan = select_targets(self.plan, targets)
        self.store = store
        self.publisher = publisher if publisher is not None else FilePublisher(store)
        self.verify_artifacts = verify_artifacts
        self.execution_settings = settings or ExecutionSettings()
        self.only = only or []
        self.force = force or []
        self.overrides = workers or []
        self.coordinator = coordinator
        self.idle_wait = idle_wait
        if not math.isfinite(idle_wait) or idle_wait < 0:
            raise ValueError("idle wait must be finite and nonnegative")
        self.run_id = uuid4().hex
        self.error: BaseException | None = None
        self.outcomes: list[Outcome] = []
        self.worker_starts = 0
        self.computation_keys: set[str] = set()
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
        self._states: list[State] = []
        self._active: str | None = None
        self._active_start = 0.0
        self._elapsed: dict[str, float] = {}
        self._started = 0.0
        self._settings = self._validate_settings()
        self.log_path = Path(log_path) if log_path is not None else None
        self.logger = logging.getLogger(f"provium-pipeline.{self.run_id}")
        self.logger.setLevel(logging.INFO)
        self._handler = (
            logging.FileHandler(self.log_path)
            if self.log_path is not None
            else logging.StreamHandler()
        )
        self.logger.addHandler(self._handler)
        self.logger.propagate = False

    def _matches(self, node: str, selectors: list[str]) -> bool:
        return matches(self.plan.configuration, node, selectors)

    def _validate_settings(self) -> dict[str, Settings]:
        return resolve_settings(
            self.plan.configuration,
            self.execution_settings,
            only=self.only,
            force=self.force,
            overrides=self.overrides,
        )

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
                f"pipeline executor failed; see {self.log_path or 'stderr'}"
            ) from self.error

    def inspect_inputs(self, items: list[InputSet]) -> list[dict[str, Any]]:
        if self._thread is not None or self._closed:
            raise RuntimeError("inspection requires an unused, open context")
        reports: list[dict[str, Any]] = []
        for item in items:
            self._add(item)
            state = self._states[-1]
            plan = state.plan or self.plan
            steps: dict[str, Any] = {}
            for node in plan.order:
                job = self._job(state, node)
                if job is None:
                    steps[node] = {"state": "blocked"}
                    continue
                try:
                    manifest = (
                        self.coordinator.result(self.store.namespace, job.key)
                        if self.coordinator
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
                for name, ref in plan.configuration.outputs.items()
                if ref in state.values
            }
            report = _outcome_json(
                Outcome(
                    item,
                    "failed"
                    if state.failed
                    else "completed"
                    if len(outputs) == len(plan.configuration.outputs)
                    else "partial",
                    outputs,
                    error=state.error,
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
            global_hard = self.execution_settings.hard_time_limit
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
            if self.coordinator is None:
                continue
            with self._lock:
                leases = list(self._leases.values())
            for lease in leases:
                try:
                    valid = self.coordinator.renew(lease)
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
        for limit in (
            self.execution_settings.soft_time_limit,
            self.execution_settings.hard_time_limit,
        ):
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
        label = "Input set"
        try:
            destinations = item.output_destinations
            if destinations is not None:
                state.plan = select_targets(self.plan, list(destinations))
            else:
                state.plan = self.plan
            expected_names = set(state.plan.configuration.inputs)
            allowed_names = (
                set(self.plan.input_types)
                if destinations is not None
                else expected_names
            )
            if (
                expected_names - item.inputs.keys()
                or item.inputs.keys() - allowed_names
            ):
                missing = ", ".join(sorted(expected_names - set(item.inputs))) or "none"
                extra = ", ".join(sorted(set(item.inputs) - allowed_names)) or "none"
                raise ValueError(  # noqa: TRY301 -- converted to an item outcome
                    f"input names do not match pipeline definition "
                    f"(missing: {missing}; unexpected: {extra})"
                )
            for name, value in item.inputs.items():
                if name not in expected_names:
                    continue
                location = (
                    value.location
                    if isinstance(value, Artifact)
                    else value.get("location")
                    if isinstance(value, dict)
                    else value
                )
                label = f"Input {name!r} ({location})"
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
                    raise ValueError(  # noqa: TRY301 -- converted to an item outcome
                        f"incompatible pipeline input: {name}; "
                        f"expected {expected}, found {artifact.fingerprint['type']}"
                    )
                state.values[f"$inputs.{name}"] = artifact
        except (OSError, ValueError) as error:
            state.failed = True
            if isinstance(error, FileNotFoundError):
                reason = "file not found; check the input path"
            elif isinstance(error, IsADirectoryError):
                reason = "expected a file, found a directory"
            elif isinstance(error, PermissionError):
                reason = "permission denied; check file access permissions"
            else:
                reason = str(error)
            state.error = f"{label}: {reason}"
            self.logger.error("%s", state.error)  # noqa: TRY400 -- expected input errors need no traceback
        except Exception:
            state.failed = True
            state.error = traceback.format_exc()
            self.logger.exception("invalid input set %s", identity)

    def _record(self, state: State, *, final: bool = False) -> None:
        identity = state.identity if state.identity is not None else id(state)
        if identity in self._pipeline_seen:
            return
        plan = state.plan or self.plan
        outputs = {
            name: state.values[ref]
            for name, ref in plan.configuration.outputs.items()
            if ref in state.values
        }
        destinations = state.item.output_destinations
        if destinations is not None and (
            final or state.failed or len(outputs) == len(plan.configuration.outputs)
        ):
            try:
                for name, artifact in outputs.items():
                    state.published[name] = self.publisher.publish(
                        artifact, destinations[name], overwrite=bool(self.force)
                    )
                if len(outputs) != len(plan.configuration.outputs) and not state.failed:
                    raise ValueError(  # noqa: TRY301 -- reported as an item failure
                        "requested outputs unavailable; "
                        "required procedures may be filtered out"
                    )
            except Exception as error:
                state.failed = True
                state.error = f"output publication: {error}"
                self.logger.error("%s", state.error)  # noqa: TRY400
            outputs = dict(state.published)
        status = (
            "failed"
            if state.failed
            else "completed"
            if len(outputs) == len(plan.configuration.outputs)
            else "cancelled"
            if self._stop.is_set()
            else "partial"
        )
        self._progress(state, status, outputs)
        if final or status in ("completed", "failed"):
            self._pipeline_seen.add(identity)
            event = Outcome(state.item, status, outputs, error=state.error)
            self.outcomes.append(event)
            self._notify(event)

    def _job(self, state: State, name: str) -> Job | None:
        if (
            state.failed
            or name in state.steps
            or state.plan is not None
            and name not in state.plan.configuration.nodes
        ):
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
        self.computation_keys.add(key)
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

    def _progress(
        self, state: State, status: str, outputs: Mapping[str, Artifact]
    ) -> None:
        """Hook for durable execution adapters."""

    def _failure(self, job: Job, error: str) -> None:
        self._failed[job.key] = error
        job.state.failed = True
        job.state.error = error
        job.state.steps.add(job.node)
        self.logger.error(
            "node=%s computation=%s input=%s: %s",
            job.node,
            job.key,
            job.state.identity,
            error,
        )
        if self.coordinator and job.lease:
            self.coordinator.fail(job.lease, error)
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
                        self.coordinator.result(self.store.namespace, job.key)
                        if self.coordinator
                        else self.store.manifest(job.key)
                    )
                    if manifest is not None and not forced:
                        self._accept(
                            job,
                            self.store.verify(manifest, job.expected)
                            if self.verify_artifacts
                            else self.store.restore(manifest, job.expected),
                            True,
                        )
                    elif self._allowed(name):
                        ready.append(job)
                except Exception:
                    self._failure(job, traceback.format_exc())
        return ready

    def _materialize(self, job: Job) -> None:
        node = self.plan.configuration.nodes[job.node]

        def resolve(reference: str) -> str:
            artifact = self.store.materialize(job.state.values[reference])
            job.state.values[reference] = artifact
            assert artifact.path is not None
            return artifact.path

        for bindings, paths in (
            (node.inputs, job.inputs),
            (node.setup_inputs, job.setup),
        ):
            for name, value in bindings.items():
                paths[name] = (
                    None
                    if value is None
                    else [resolve(ref) for ref in value]
                    if isinstance(value, list)
                    else resolve(value)
                )

    def _run_node(self, first: Job, *, keep_workers: bool = False) -> None:
        from provium_pipeline.workers import Worker

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
                    try:
                        self._materialize(job)
                    except Exception:
                        self._failure(job, traceback.format_exc())
                        continue
                    if self.coordinator:
                        job.lease = self.coordinator.acquire(
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
                    if not progress and self.coordinator:
                        break
                time.sleep(0.01)
        finally:
            if not keep_workers:
                self._unload_node()

    def _publish(self, job: Job, outputs: dict[str, Any]) -> None:
        try:
            if self.coordinator and job.lease:
                committed = self.coordinator.complete(job.lease, outputs)
            else:
                self.store.commit(job.key, outputs)
                committed = True
        except Exception:
            # Publication is idempotently reconciled; the procedure is never retried.
            observed = (
                self.coordinator.result(self.store.namespace, job.key)
                if self.coordinator
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
                if self.coordinator is None:
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
            self._execute(items)

    def _execute(self, items: Iterable[InputSet]) -> None:
        items = list(items)
        if any(item.output_destinations is not None for item in items):
            self.publisher.assert_requests_are_valid(items)
        if self.strategy == "item":
            states: list[State] = []
            for item in items:
                self._states = []
                self._add(item)
                states.extend(self._states)
                self._run_local()
            self._states = states
        else:
            for item in items:
                self._add(item)
            self._run_local()

    def _run_local(self) -> None:
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
            if self._states and all(
                state.failed
                or all(
                    ref in state.values
                    for ref in (state.plan or self.plan).configuration.outputs.values()
                )
                for state in self._states
            ):
                break
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
