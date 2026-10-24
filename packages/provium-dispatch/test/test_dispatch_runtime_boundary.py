from unittest.mock import Mock

import pytest
from provium_pipeline import ArtifactStore
from provium_text_pipeline_example.artifact import DOCUMENT, TOKENS
from test_runtime import configuration

from provium_dispatch import ComputeProfile, ExecutionContext, PipelineDefinition


def context(tmp_path, **options):
    return ExecutionContext(
        configuration(), ArtifactStore(str(tmp_path / "store")), **options
    )


def test_profile_allowlist(tmp_path):
    definition = PipelineDefinition({"document": DOCUMENT}, {"tokens": TOKENS})
    with pytest.raises(ValueError, match="profile not allowed"):
        context(tmp_path, definition=definition, profile=ComputeProfile(name="denied"))


def test_registered_definition_cannot_be_changed(tmp_path):
    backend = Mock()
    definition = PipelineDefinition({"document": DOCUMENT}, {"tokens": TOKENS})
    backend.definition.return_value = definition
    with context(tmp_path, backend=backend, definition=definition):
        pass
    with pytest.raises(ValueError, match="snapshot"):
        context(tmp_path, backend=backend, definition=PipelineDefinition({}, {}))


def test_queue_snapshot_tampering_is_rejected(tmp_path):
    with context(tmp_path) as execution:
        with pytest.raises(ValueError, match="pipeline snapshot"):
            execution._load_page([{"id": 1, "snapshot": {}}])
        with pytest.raises(ValueError, match="definition"):
            execution._load_page(
                [
                    {
                        "id": 1,
                        "snapshot": execution.plan.configuration.model_dump(
                            mode="json"
                        ),
                        "definition": {},
                    }
                ]
            )
