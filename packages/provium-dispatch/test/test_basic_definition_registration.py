"""Basic definitions remain registerable without adding dispatch fields."""

import os
from uuid import uuid4

import pytest
from provium_pipeline import ArtifactStore, PipelineConfiguration, PipelineDefinition
from test_store import make_artifact

from provium_dispatch import ExecutionContext
from provium_dispatch.persistence.backends.postgres import PostgresBackend


@pytest.mark.skipif(
    not os.environ.get("PROVIUM_TEST_DATABASE"), reason="requires PostgreSQL"
)
def test_register_and_execute_basic_definition(tmp_path):
    backend = PostgresBackend(os.environ["PROVIUM_TEST_DATABASE"])
    kind = "provium_text_pipeline_example.DocumentV1"
    graph = PipelineConfiguration(
        name="basic-" + uuid4().hex,
        inputs={"source": kind},
        nodes={},
        targets={"copy": "$inputs.source"},
    )
    definition = PipelineDefinition({"source": kind}, {"copy": kind})
    backend.register(
        [graph.model_dump(mode="json")], definitions={graph.name: definition}
    )
    source = str(make_artifact(tmp_path / "source.pa"))
    backend.enqueue(graph.name, {"source": source})
    with ArtifactStore(str(tmp_path / "cache")) as store:
        with ExecutionContext(
            graph, store, backend=backend, definition=definition
        ) as context:
            context.start()
        assert context.outcomes[0].state == "completed"
