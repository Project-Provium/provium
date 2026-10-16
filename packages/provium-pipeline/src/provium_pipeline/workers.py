"""Spawned procedure workers; only paths and plain outcome messages cross pipes."""

from __future__ import annotations

import multiprocessing as mp
import traceback
from dataclasses import dataclass
from typing import Any, cast

from provium import ProcedureExecutor, session


def bindings(procedure: Any, scope: str, paths: dict[str, Any]) -> dict[str, Any]:
    record = getattr(procedure.resolve_contract(), scope)
    if record is None:
        return {}
    result: dict[str, Any] = {}
    for name, value in paths.items():
        artifact = record.fields[name].artifact.resolve()
        bind = artifact.bind_write if scope == "Outputs" else artifact.bind_read
        result[name] = (
            None
            if value is None
            else [bind(path) for path in cast(list[str], value)]
            if isinstance(value, list)
            else bind(value)
        )
    return result


def worker_main(
    connection: Any,
    procedure: Any,
    configuration: dict[str, Any],
    setup: dict[str, Any],
) -> None:
    try:
        with session():
            prepared = ProcedureExecutor().prepare(
                procedure,
                configuration_layers=[configuration],
                setup_inputs=bindings(procedure, "SetupInputs", setup),
            )
            try:
                connection.send(("ready", None))
                while True:
                    command = connection.recv()
                    if command is None:
                        break
                    inputs, outputs = command
                    try:
                        prepared.execute(
                            inputs=bindings(procedure, "Inputs", inputs),
                            outputs=bindings(procedure, "Outputs", outputs),
                        )
                        connection.send(("done", outputs))
                    except Exception:
                        connection.send(("error", traceback.format_exc()))
            finally:
                prepared.close()
    except (EOFError, BrokenPipeError):
        pass
    except BaseException:
        try:
            connection.send(("error", traceback.format_exc()))
        except (EOFError, BrokenPipeError):
            pass
    finally:
        connection.close()


@dataclass
class Worker:
    process: Any
    connection: Any
    ready: bool = False
    job: Any = None

    @classmethod
    def start(
        cls, procedure: Any, configuration: dict[str, Any], setup: dict[str, Any]
    ) -> Worker:
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=worker_main, args=(child, procedure, configuration, setup)
        )
        process.start()
        child.close()
        return cls(process, parent)

    def close(self, *, terminate: bool = False) -> None:
        if self.process.is_alive():
            if terminate:
                self.process.terminate()
            else:
                try:
                    self.connection.send(None)
                except (BrokenPipeError, EOFError):
                    pass
            self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join()
        else:
            self.process.join()
        self.connection.close()
        self.process.close()
