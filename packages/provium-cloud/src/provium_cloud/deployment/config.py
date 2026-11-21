"""Unnamed deployment configuration and ordered scheduling rules."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
)

Text = Annotated[str, Field(strict=True, min_length=1, pattern=r"^\S+$")]


def _seconds(value: object) -> object:
    if isinstance(value, str):
        match = re.fullmatch(r"([1-9][0-9]*)([smh])", value)
        if match:
            return int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]
    return value


Timeout = Annotated[int, Field(strict=True, ge=60), BeforeValidator(_seconds)]


class _Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class BackendConfiguration(_Configuration):
    """Backend selection; the selected backend validates its own options."""

    backend: Text
    options: dict[str, JsonValue] = Field(default_factory=dict)


class SchedulingOptions(_Configuration):
    """Partial overrides; omitted and null values retain previous settings."""

    job_queue: Text | None = None
    job_definition: Text | None = None
    timeout: Timeout | None = None


class JobSettings(_Configuration):
    """Resolved AWS Batch settings, ready for submission."""

    job_queue: Text
    job_definition: Text
    timeout: Timeout | None = None


class SchedulingRule(SchedulingOptions):
    match: Annotated[
        str,
        Field(pattern=r"^(?:(?:nodes|tags)\.[^.$\s/]+|procedures\.\S+)$"),
    ]


class CloudDeployment(_Configuration):
    """Scheduling configuration supplied directly, without registration."""

    kind: Literal["CloudDeployment"] = "CloudDeployment"
    provider: Literal["aws"]
    region: Text
    artifacts: BackendConfiguration
    state: BackendConfiguration
    defaults: SchedulingOptions = Field(default_factory=SchedulingOptions)
    routes: list[SchedulingRule] = Field(default_factory=list[SchedulingRule])

    def resolve(
        self,
        *,
        procedure: str,
        node: str | None = None,
        tags: Iterable[str] = (),
        overrides: SchedulingOptions | Mapping[str, object] | None = None,
    ) -> JobSettings:
        """Apply defaults, matching routes in order, then explicit overrides."""
        selectors = {f"procedures.{procedure}", *(f"tags.{tag}" for tag in tags)}
        if node is not None:
            selectors.add(f"nodes.{node}")
        values = self.defaults.model_dump(exclude_none=True)
        for rule in self.routes:
            if rule.match in selectors:
                values.update(rule.model_dump(exclude={"match"}, exclude_none=True))
        if overrides is not None:
            values.update(
                SchedulingOptions.model_validate(overrides).model_dump(
                    exclude_none=True
                )
            )
        return JobSettings.model_validate(values)
