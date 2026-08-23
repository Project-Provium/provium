from typing import Any

import pytest
from pydantic import ConfigDict

from provium import (
    ProcedureCatalog,
    ProcedureConfig,
    ProcedureConfigurationError,
    ProcedureContract,
    ProcedureDefinition,
    ProcedureInputs,
    ProcedureOutputs,
)
from provium_pipeline.compiler import (
    PipelineConfiguration,
    PipelineConfigurationLayer,
    PipelineConfigurationResolutionError,
    ProcedureCatalogCollection,
    resolve_pipeline_configuration,
)
from provium_pipeline.definition import PipelineBuilder, PipelineDefinition


class ExampleConfig(ProcedureConfig):
    model_config = ConfigDict(extra="forbid")

    threshold: float = 0.5
    nested: dict[str, int] = {"left": 0, "right": 0}
    label: str = "default"


class ConfiguredContract(ProcedureContract[ExampleConfig]):
    configuration = ExampleConfig

    class SetupInputs(ProcedureInputs):
        pass

    class Inputs(ProcedureInputs):
        pass

    class Outputs(ProcedureOutputs):
        pass


class PlainContract(ProcedureContract[None]):
    class SetupInputs(ProcedureInputs):
        pass

    class Inputs(ProcedureInputs):
        pass

    class Outputs(ProcedureOutputs):
        pass


CONFIGURED: ProcedureDefinition[Any] = ProcedureDefinition(
    identifier="example.ConfiguredV1",
    target="missing_implementation:ConfiguredV1",
    label="Configured",
    description="configured",
    contract=ConfiguredContract,
)
PLAIN: ProcedureDefinition[Any] = ProcedureDefinition(
    identifier="example.PlainV1",
    target="missing_implementation:PlainV1",
    label="Plain",
    description="plain",
    contract=PlainContract,
)


def configured_definition() -> PipelineDefinition:
    builder = PipelineBuilder(identifier="configured-pipeline", version="1")
    builder.node(
        "configured",
        CONFIGURED,
        config={"threshold": 0.1, "nested": {"left": 1, "right": 1}},
    )
    return builder.build()


def catalogs(*definitions: ProcedureDefinition[Any]) -> ProcedureCatalogCollection:
    catalog = ProcedureCatalog()
    for definition in definitions:
        catalog.register(definition)
    return ProcedureCatalogCollection((catalog,))


def test_resolution_composes_layers_validates_and_includes_defaults() -> None:
    layers = (
        PipelineConfigurationLayer(
            source="first.yaml",
            configuration=PipelineConfiguration.model_validate(
                {"nodes": {"configured": {"nested": {"left": 2}}}}
            ),
        ),
        PipelineConfigurationLayer(
            source="second.json",
            configuration=PipelineConfiguration.model_validate(
                {"nodes": {"configured": {"threshold": 0.9}}}
            ),
        ),
    )

    resolved = resolve_pipeline_configuration(
        configured_definition(), catalogs(CONFIGURED), layers
    )

    node = resolved.nodes[0]
    assert node.node == "configured"
    assert node.source_layers == (
        "pipeline node 'configured' inline config",
        "first.yaml",
        "second.json",
    )
    assert node.snapshot is not None
    assert node.snapshot.value == {
        "label": "default",
        "nested": {"left": 2, "right": 1},
        "threshold": 0.9,
    }
    assert resolved.document == {
        "schema": "provium.pipeline-config/v1",
        "nodes": {"configured": node.snapshot.value},
    }
    assert len(resolved.document_digest) == 64
    assert [layer.source for layer in resolved.layers] == ["first.yaml", "second.json"]
    assert all(len(layer.digest) == 64 for layer in resolved.layers)


def test_resolution_does_not_import_concrete_procedure_target() -> None:
    resolved = resolve_pipeline_configuration(
        configured_definition(), catalogs(CONFIGURED)
    )

    assert resolved.nodes[0].snapshot is not None


def test_configured_defaults_resolve_without_contributing_values() -> None:
    builder = PipelineBuilder(identifier="defaults-pipeline", version="1")
    builder.node("configured", CONFIGURED)
    empty_layer = PipelineConfigurationLayer(
        source="empty.yaml",
        configuration=PipelineConfiguration(),
    )

    resolved = resolve_pipeline_configuration(
        builder.build(), catalogs(CONFIGURED), (empty_layer,)
    )

    node = resolved.nodes[0]
    assert node.source_layers == ()
    assert node.snapshot is not None
    assert node.snapshot.value == {
        "label": "default",
        "nested": {"left": 0, "right": 0},
        "threshold": 0.5,
    }


def test_unconfigured_node_with_empty_mapping_has_no_snapshot() -> None:
    builder = PipelineBuilder(identifier="plain-pipeline", version="1")
    builder.node("plain", PLAIN)

    resolved = resolve_pipeline_configuration(builder.build(), catalogs(PLAIN))

    assert resolved.nodes[0].snapshot is None
    assert resolved.document["nodes"] == {"plain": {}}


def test_unconfigured_node_requires_empty_resolved_mapping() -> None:
    builder = PipelineBuilder(identifier="plain-pipeline", version="1")
    builder.node("plain", PLAIN, config={"unexpected": True})

    with pytest.raises(
        PipelineConfigurationResolutionError,
        match="plain.*does not accept configuration",
    ):
        resolve_pipeline_configuration(builder.build(), catalogs(PLAIN))


def test_external_layer_rejects_unknown_node() -> None:
    layer = PipelineConfigurationLayer(
        source="unknown.yaml",
        configuration=PipelineConfiguration.model_validate(
            {"nodes": {"unknown": {"threshold": 1.0}}}
        ),
    )

    with pytest.raises(
        PipelineConfigurationResolutionError, match="unknown.yaml.*unknown"
    ):
        resolve_pipeline_configuration(
            configured_definition(), catalogs(CONFIGURED), (layer,)
        )


def test_validation_error_preserves_ordered_source_layers() -> None:
    layer = PipelineConfigurationLayer(
        source="invalid.yaml",
        configuration=PipelineConfiguration.model_validate(
            {"nodes": {"configured": {"threshold": "invalid"}}}
        ),
    )

    with pytest.raises(ProcedureConfigurationError) as captured:
        resolve_pipeline_configuration(
            configured_definition(), catalogs(CONFIGURED), (layer,)
        )

    assert captured.value.source_layers == (
        "pipeline node 'configured' inline config",
        "invalid.yaml",
    )
