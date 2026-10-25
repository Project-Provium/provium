"""Execution requests and adapters for input sources."""

from __future__ import annotations

import csv
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, TextIO

from provium_pipeline.models import REFERENCE, name_check
from provium_pipeline.store import Artifact


@dataclass(frozen=True)
class InputSet:
    """Legacy input-only request, retained while durable execution is extracted."""

    inputs: Mapping[str, str | Artifact | dict[str, Any]]

    @property
    def output_destinations(self) -> Mapping[str, str] | None:
        return None

    def __post_init__(self) -> None:
        object.__setattr__(self, "inputs", MappingProxyType(dict(self.inputs)))


@dataclass(frozen=True)
class ExecutionRequest(InputSet):
    destinations: Mapping[str, str]

    @property
    def output_destinations(self) -> Mapping[str, str]:
        return self.destinations

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.destinations or any(
            not key or not isinstance(path, str) or not path
            for key, path in self.destinations.items()
        ):
            raise ValueError("an execution request requires output destinations")
        object.__setattr__(
            self, "destinations", MappingProxyType(dict(self.destinations))
        )


class RequestSource(Protocol):
    def __iter__(self) -> Iterator[ExecutionRequest]: ...


class CSVSource:
    """Read input columns named inputs.NAME and output columns named by reference.

    Paths are interpreted relative to the process working directory, like CLI
    bindings. The caller owns the stream and keeps it open during iteration.
    """

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    def __iter__(self) -> Iterator[ExecutionRequest]:
        reader = csv.reader(self.stream, strict=True)
        headers = next(reader, list[str]())
        if not headers or len(set(headers)) != len(headers):
            raise ValueError("CSV requires a nonempty, unique header")
        input_columns: dict[int, str] = {}
        output_columns: dict[int, str] = {}
        for index, header in enumerate(headers):
            if header.startswith("inputs."):
                input_columns[index] = name_check(header[7:])
            else:
                if not (
                    header.startswith("nodes.") and REFERENCE.fullmatch("$" + header)
                ):
                    name_check(header)
                output_columns[index] = header
        if not output_columns:
            raise ValueError("CSV requires at least one output destination column")
        for row in reader:
            if len(row) != len(headers):
                raise ValueError(
                    f"CSV row {reader.line_num}: expected {len(headers)} cells"
                )
            inputs = {name: row[i] for i, name in input_columns.items() if row[i]}
            outputs = {name: row[i] for i, name in output_columns.items() if row[i]}
            if not outputs:
                raise ValueError(f"CSV row {reader.line_num}: no output destinations")
            yield ExecutionRequest(inputs, outputs)
