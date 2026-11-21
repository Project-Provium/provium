"""Execute one claimed computation in an isolated, cancellable process."""

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic
from typing import Any

from provium.procedure.discovery import discover_procedure_catalogs
from provium_pipeline.workers import Worker

from provium_cloud.artifacts import ArtifactReference
from provium_cloud.client import CloudClient
from provium_cloud.deployment import CloudDeployment
from provium_cloud.invocations.planning import resolve_record
from provium_cloud.state.models import Generation


def _execute(
    client: CloudClient, key: str, generation: Generation, lost: Event, interval: float
) -> dict[str, ArtifactReference]:
    started = monotonic()
    payload = generation.payload
    definition = discover_procedure_catalogs().resolve(payload["procedure"])
    record, _ = resolve_record(definition, payload["configuration"])
    if record.to_dict() != payload["record"]:
        raise ValueError("worker contract/configuration differs from the admitted plan")

    def materialize(value: Any) -> Any:
        if value is None:
            return None
        if "value_input" in value:
            return value["value_input"]["value"]
        return client.artifacts.materialize(value)

    inputs = {name: materialize(value) for name, value in payload["inputs"].items()}
    setup = {
        name: materialize(value) for name, value in payload["setup_inputs"].items()
    }
    with TemporaryDirectory(prefix="provium-cloud-") as directory:
        outputs = {
            name: str(Path(directory) / f"{index}.pa")
            for index, name in enumerate(payload["expected"])
        }
        worker = Worker.start(definition, payload["configuration"], setup)
        try:
            _wait(
                worker,
                inputs,
                outputs,
                lost,
                interval,
                started,
                generation.settings.timeout,
            )
        finally:
            worker.close(terminate=True)
        if lost.is_set():
            raise RuntimeError("worker lost its execution lease")
        return client.artifacts.stage(key, outputs, payload["expected"])


def _wait(
    worker: Worker,
    inputs: dict[str, Any],
    outputs: dict[str, str],
    lost: Event,
    interval: float,
    started: float,
    timeout: int | None,
) -> None:
    while True:
        if lost.is_set():
            raise RuntimeError("worker lost its execution lease")
        if timeout is not None and monotonic() - started > timeout:
            raise TimeoutError("procedure exceeded its execution timeout")
        if worker.connection.poll(min(1, interval)):
            kind, result = worker.connection.recv()
            if kind == "error":
                raise RuntimeError(result)
            if kind == "ready":
                worker.connection.send((inputs, outputs))
            elif kind == "done":
                return
            else:
                raise RuntimeError(f"unexpected worker message: {kind}")
        elif not worker.process.is_alive():
            raise RuntimeError("procedure worker exited without a result")


def execute_job(
    deployment: CloudDeployment,
    key: str,
    number: int,
    attempt: str,
    *,
    job_id: str | None = None,
    lease_seconds: float = 60,
) -> bool:
    with CloudClient(deployment) as client:
        owner = client.coordinator.claim(
            key, number, attempt, job_id=job_id, ttl=lease_seconds
        )
        if owner is None:
            return False
        generation = client.coordinator.get(key).generations[number - 1]
        stop, lost = Event(), Event()

        def heartbeat() -> None:
            while not stop.wait(lease_seconds / 3):
                try:
                    if not client.coordinator.heartbeat(
                        key, number, owner, ttl=lease_seconds
                    ):
                        lost.set()
                        return
                except Exception:
                    lost.set()
                    return

        thread = Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            references = _execute(client, key, generation, lost, lease_seconds / 3)
            return not lost.is_set() and client.coordinator.complete(
                key, number, owner, references
            )
        except BaseException as error:
            client.coordinator.fail(key, number, owner, str(error))
            raise
        finally:
            stop.set()
            thread.join(timeout=lease_seconds)
