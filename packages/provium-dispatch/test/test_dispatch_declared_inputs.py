import os
from uuid import uuid4

import pytest
from provium_text_pipeline_example.artifact import TOKENS
from test_declared_inputs import graph


@pytest.mark.skipif(
    not os.environ.get("PROVIUM_TEST_DATABASE"), reason="requires PostgreSQL"
)
def test_enqueue_enforces_declarations_without_definition(tmp_path):
    from test_store import make_artifact

    from provium_dispatch.persistence.backends.postgres import PostgresBackend

    backend = PostgresBackend(os.environ["PROVIUM_TEST_DATABASE"])
    document = graph(name="declared-" + uuid4().hex).model_dump(mode="json")
    wrong = graph(
        name="declared-" + uuid4().hex, inputs={"document": TOKENS.identifier}
    ).model_dump(mode="json")
    source = str(make_artifact(tmp_path / "source.pa"))
    backend.register([document, wrong])
    try:
        for inputs in ({}, {"document": source, "extra": source}):
            with pytest.raises(ValueError, match="input names"):
                backend.enqueue(document["name"], inputs)
        with pytest.raises(ValueError, match="incompatible pipeline input"):
            backend.enqueue(wrong["name"], {"document": source})
        assert backend.pending(document["name"]) == []
        identity = backend.enqueue(document["name"], {"document": source})
        assert backend.pending(document["name"])[0]["id"] == identity
    finally:
        backend.unregister([document, wrong])
