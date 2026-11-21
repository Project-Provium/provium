"""Compute provider boundary; durable decisions belong to the state backend."""

from typing import Protocol

from provium_cloud.deployment import CloudDeployment
from provium_cloud.state.models import Generation


class Executor(Protocol):
    def submit(
        self, key: str, generation: Generation, deployment: CloudDeployment
    ) -> str: ...
    def find(self, generation: Generation) -> list[str]: ...
    def describe(self, jobs: list[str]) -> dict[str, str]: ...
    def cancel(self, job: str, status: str, reason: str) -> None: ...
