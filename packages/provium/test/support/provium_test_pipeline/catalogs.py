"""Lightweight catalogs for the integration test pipeline."""

from provium import ArtifactCatalog, ProcedureCatalog
from support.provium_test_pipeline.contracts import (
    FAILING_PROCEDURE,
    SOURCE_PROCEDURE,
    TEXT_ARTIFACT,
    TRANSFORM_PROCEDURE,
)

artifact_catalog = ArtifactCatalog()
artifact_catalog.register(TEXT_ARTIFACT)

procedure_catalog = ProcedureCatalog()
procedure_catalog.register(SOURCE_PROCEDURE)
procedure_catalog.register(TRANSFORM_PROCEDURE)
procedure_catalog.register(FAILING_PROCEDURE)
