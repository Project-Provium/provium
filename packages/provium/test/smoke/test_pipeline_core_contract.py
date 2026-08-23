"""Compatibility audit for the public core surface consumed by provium-pipeline."""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass

import pytest


@dataclass(frozen=True, slots=True)
class PublicContract:
    module: str
    name: str


PIPELINE_CORE_CONTRACTS = (
    PublicContract("provium", "ArtifactDefinition"),
    PublicContract("provium", "Artifact"),
    PublicContract("provium", "ArtifactReadBinding"),
    PublicContract("provium", "ArtifactWriteBinding"),
    PublicContract("provium", "ProcedureDefinition"),
    PublicContract("provium", "ProcedureContract"),
    PublicContract("provium", "ProcedureInputs"),
    PublicContract("provium", "ProcedureOutputs"),
    PublicContract("provium", "Procedure"),
    PublicContract("provium", "ProcedureExecutor"),
    PublicContract("provium", "PreparedProcedure"),
    PublicContract("provium", "ProcedureConfig"),
    PublicContract("provium", "ConfigurationSnapshot"),
    PublicContract("provium", "ProcedureOutputResult"),
    PublicContract("provium", "FinalizedArtifactInspection"),
    PublicContract("provium", "inspect_finalized_artifact"),
)


def test_core_api_version_is_public() -> None:
    provium = importlib.import_module("provium")

    assert provium.PROVIUM_CORE_API_VERSION == 1
    assert isinstance(provium.PROVIUM_CORE_API_VERSION, int)


@pytest.mark.parametrize("contract", PIPELINE_CORE_CONTRACTS)
def test_pipeline_core_contract_is_public_and_inspectable(
    contract: PublicContract,
) -> None:
    module = importlib.import_module(contract.module)
    value = getattr(module, contract.name)

    assert value.__module__.startswith("provium")
    assert inspect.signature(value)
