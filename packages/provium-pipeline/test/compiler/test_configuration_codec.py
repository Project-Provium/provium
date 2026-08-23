import json
from typing import Protocol

import pytest
from pydantic import ValidationError

from provium_pipeline.compiler import (
    PipelineConfiguration,
    PipelineConfigurationLoadError,
    canonical_configuration_document,
    load_pipeline_configuration_json,
    load_pipeline_configuration_yaml,
)


class ConfigurationLoader(Protocol):
    def __call__(
        self, document: str, *, source: str | None = None
    ) -> PipelineConfiguration: ...


JSON_DOCUMENT = json.dumps(
    {
        "schema": "provium.pipeline-config/v1",
        "nodes": {
            "detect": {"confidence": 0.6, "nested": {"left": 1}},
            "track": {"threshold": 0.45},
        },
    }
)

YAML_DOCUMENT = """
schema: provium.pipeline-config/v1
nodes:
  detect:
    confidence: 0.6
    nested:
      left: 1
  track:
    threshold: 0.45
"""


def test_json_and_yaml_configuration_documents_are_equivalent() -> None:
    from_json = load_pipeline_configuration_json(JSON_DOCUMENT, source="config.json")
    from_yaml = load_pipeline_configuration_yaml(YAML_DOCUMENT, source="config.yaml")

    assert from_json == from_yaml
    assert canonical_configuration_document(from_json) == {
        "schema": "provium.pipeline-config/v1",
        "nodes": {
            "detect": {"confidence": 0.6, "nested": {"left": 1}},
            "track": {"threshold": 0.45},
        },
    }


def test_configuration_model_is_strict_frozen_and_rejects_extra_fields() -> None:
    configuration = load_pipeline_configuration_json(JSON_DOCUMENT)

    with pytest.raises(ValidationError, match="frozen"):
        configuration.nodes = {}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PipelineConfiguration.model_validate(
            {"schema": "provium.pipeline-config/v1", "nodes": {}, "extra": True}
        )


@pytest.mark.parametrize(
    ("loader", "document", "source"),
    [
        (load_pipeline_configuration_json, "{", "broken.json"),
        (load_pipeline_configuration_json, "[]", "not-a-mapping.json"),
        (load_pipeline_configuration_yaml, "nodes: [", "broken.yaml"),
        (
            load_pipeline_configuration_json,
            '{"schema":"wrong","nodes":{}}',
            "invalid.json",
        ),
    ],
)
def test_configuration_load_errors_include_source_and_failure_chain(
    loader: ConfigurationLoader, document: str, source: str
) -> None:
    with pytest.raises(PipelineConfigurationLoadError, match=source) as captured:
        loader(document, source=source)

    assert captured.value.__cause__ is not None
