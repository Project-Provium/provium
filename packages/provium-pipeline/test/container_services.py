"""Test-owned services, started before module-level integration skip markers."""

from contextlib import ExitStack

import pytest
from testcontainers.community.localstack import LocalStackContainer
from testcontainers.community.postgres import PostgresContainer

_STARTED = pytest.StashKey[set[str]]()


def start_services(config, *, postgres=False):
    started = config.stash.setdefault(_STARTED, set())
    stack = ExitStack()
    config.add_cleanup(stack.close)
    env = stack.enter_context(pytest.MonkeyPatch.context())
    if "s3" not in started:
        container = LocalStackContainer(
            image="localstack/localstack:4", region_name="us-east-1"
        )
        container.with_services("s3")
        localstack = stack.enter_context(container)
        env.setenv("AWS_ENDPOINT_URL_S3", localstack.get_url())
        env.setenv("AWS_ACCESS_KEY_ID", "test")
        env.setenv("AWS_SECRET_ACCESS_KEY", "test")
        env.setenv("AWS_DEFAULT_REGION", "us-east-1")
        env.delenv("AWS_SESSION_TOKEN", raising=False)
        env.delenv("AWS_PROFILE", raising=False)
        env.setenv("PROVIUM_TEST_S3", "1")
        started.add("s3")
    if postgres and "postgres" not in started:
        database = stack.enter_context(
            PostgresContainer("postgres:17-alpine", driver=None)
        )
        env.setenv("PROVIUM_TEST_DATABASE", database.get_connection_url())
        started.add("postgres")
