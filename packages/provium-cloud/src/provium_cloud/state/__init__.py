"""State backend selection, independent of dispatch and cloud execution."""

import os
from importlib.metadata import entry_points
from typing import cast

from provium_cloud.deployment import BackendConfiguration
from provium_cloud.state.interface import StateBackend


def open_state(config: BackendConfiguration, *, namespace: str) -> StateBackend:
    if config.backend == "sqlite":
        from provium_cloud.state.backends.sqlite import SQLiteBackend, SQLiteOptions

        return SQLiteBackend(
            SQLiteOptions.model_validate(config.options).path, namespace=namespace
        )
    if config.backend == "postgres":
        from provium_cloud.state.backends.postgres import (
            PostgresBackend,
            PostgresOptions,
        )

        options = PostgresOptions.model_validate(config.options)
        connection = os.environ.get(options.connection_env)
        if not connection:
            raise ValueError(
                f"missing state connection variable: {options.connection_env}"
            )
        return PostgresBackend(connection, namespace=namespace)
    factories = list(
        entry_points(group="provium.cloud.state_backends", name=config.backend)
    )
    if len(factories) != 1:
        raise ValueError(f"unknown or ambiguous state backend: {config.backend}")
    return cast(
        StateBackend, factories[0].load()(dict(config.options), namespace=namespace)
    )


__all__ = ["StateBackend", "open_state"]
