from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from provium.procedure.config import ConfigurationSnapshot, ProcedureConfig
from provium.procedure.definition import (
    ProcedureContractMetadata,
    ProcedureDefinition,
    ProcedureIOFieldMetadata,
)
from provium_pipeline.artifact import (
    ArtifactLocation,
    ArtifactStore,
    ManagedArtifactDescriptor,
    MaterializationCleanup,
    MaterializedArtifact,
)
from provium_pipeline.compiler.catalogs import ArtifactCatalogCollection
from provium_pipeline.compiler.models import CompiledBindingPlan
from provium_pipeline.task_executor import (
    AttemptMaterializations,
    BindingResolutionError,
    ConfigurationSnapshotMismatchError,
    PreparedProcedureCache,
    PreparedProcedureKey,
    ProcedureContractMismatchError,
    StoredArtifact,
    build_output_bindings,
    build_read_binding_value,
    materialize_binding_inputs,
    resolve_binding_references,
    verify_configuration_snapshot,
    verify_procedure_contract,
)


@dataclass
class _Prepared:
    close_count: int = 0

    def close(self) -> None:
        self.close_count += 1


def _key(*, configuration: str = "configuration-a") -> PreparedProcedureKey:
    return PreparedProcedureKey(
        procedure="procedure-a",
        configuration=configuration,
        setup="setup-a",
    )


def test_prepared_procedure_cache_reuses_stateful_setup_by_fingerprint() -> None:
    cache = PreparedProcedureCache[_Prepared]()
    prepared = _Prepared()
    preparations = 0

    def prepare() -> _Prepared:
        nonlocal preparations
        preparations += 1
        return prepared

    first = cache.get_or_prepare(_key(), prepare)
    second = cache.get_or_prepare(_key(), prepare)

    assert first is prepared
    assert second is prepared
    assert preparations == 1


def test_prepared_procedure_cache_separates_configuration_fingerprints() -> None:
    cache = PreparedProcedureCache[_Prepared]()

    first = cache.get_or_prepare(_key(), _Prepared)
    second = cache.get_or_prepare(
        _key(configuration="configuration-b"),
        _Prepared,
    )

    assert first is not second


def test_prepared_procedure_cache_closes_each_prepared_instance_once() -> None:
    cache = PreparedProcedureCache[_Prepared]()
    first = cache.get_or_prepare(_key(), _Prepared)
    second = cache.get_or_prepare(
        _key(configuration="configuration-b"),
        _Prepared,
    )

    cache.close()
    cache.close()

    assert first.close_count == 1
    assert second.close_count == 1
    with pytest.raises(RuntimeError, match="closed"):
        cache.get_or_prepare(_key(), _Prepared)


def test_prepared_procedure_cache_closes_remaining_instances_after_failure() -> None:
    cache = PreparedProcedureCache[_Prepared]()
    first = cache.get_or_prepare(_key(), _Prepared)

    class _FailingPrepared(_Prepared):
        def close(self) -> None:
            super().close()
            raise RuntimeError("close failed")

    failing = cache.get_or_prepare(
        _key(configuration="configuration-b"),
        _FailingPrepared,
    )

    with pytest.raises(RuntimeError, match="close failed"):
        cache.close()
    cache.close()

    assert failing.close_count == 1
    assert first.close_count == 1


def test_prepared_procedure_cache_preserves_first_of_multiple_close_failures() -> None:
    cache = PreparedProcedureCache[_Prepared]()

    class _FirstFailure(_Prepared):
        def close(self) -> None:
            raise RuntimeError("first inserted")

    class _SecondFailure(_Prepared):
        def close(self) -> None:
            raise RuntimeError("second inserted")

    cache.get_or_prepare(_key(), _FirstFailure)
    cache.get_or_prepare(
        _key(configuration="configuration-b"),
        _SecondFailure,
    )

    with pytest.raises(RuntimeError, match="second inserted"):
        cache.close()


class _Config(ProcedureConfig):
    count: int


def test_configuration_snapshot_is_revalidated_into_installed_model() -> None:
    snapshot = ConfigurationSnapshot.from_configuration(_Config(count=3))

    configuration = verify_configuration_snapshot(snapshot, _Config)

    assert isinstance(configuration, _Config)
    assert configuration.count == 3


def test_configuration_snapshot_rejects_value_digest_drift() -> None:
    snapshot = ConfigurationSnapshot.from_configuration(_Config(count=3))
    tampered = replace(snapshot, value={"count": 4})

    with pytest.raises(ConfigurationSnapshotMismatchError, match="value digest"):
        verify_configuration_snapshot(tampered, _Config)


def test_configuration_snapshot_rejects_invalid_frozen_value() -> None:
    snapshot = ConfigurationSnapshot.from_configuration(_Config(count=3))
    invalid = replace(snapshot, value={"count": "not-an-integer"})

    with pytest.raises(ConfigurationSnapshotMismatchError, match="value is invalid"):
        verify_configuration_snapshot(invalid, _Config)


def test_configuration_snapshot_rejects_recorded_schema_digest_drift() -> None:
    snapshot = ConfigurationSnapshot.from_configuration(_Config(count=3))
    tampered = replace(snapshot, schema_digest="tampered")

    with pytest.raises(ConfigurationSnapshotMismatchError, match="schema digest"):
        verify_configuration_snapshot(tampered, _Config)


def test_configuration_snapshot_rejects_installed_schema_drift() -> None:
    snapshot = ConfigurationSnapshot.from_configuration(_Config(count=3))

    class _ChangedConfig(ProcedureConfig):
        count: str

    with pytest.raises(ConfigurationSnapshotMismatchError, match="model target"):
        verify_configuration_snapshot(snapshot, _ChangedConfig)


def test_configuration_snapshot_requires_matching_configuration_contract() -> None:
    snapshot = ConfigurationSnapshot.from_configuration(_Config(count=3))

    with pytest.raises(ConfigurationSnapshotMismatchError, match="does not accept"):
        verify_configuration_snapshot(snapshot, None)
    with pytest.raises(ConfigurationSnapshotMismatchError, match="missing"):
        verify_configuration_snapshot(None, _Config)
    assert verify_configuration_snapshot(None, None) is None


def _binding_plan(*, minimum: int, maximum: int | None) -> CompiledBindingPlan:
    return CompiledBindingPlan(
        field="documents",
        artifact_identifier="example.document.v1",
        minimum=minimum,
        maximum=maximum,
        references=("input:first", "input:missing", "input:second"),
    )


def test_binding_references_preserve_repeated_input_order() -> None:
    identities = {
        "input:first": "sha256:first",
        "input:second": "sha256:second",
    }

    resolved = resolve_binding_references(
        _binding_plan(minimum=1, maximum=None),
        identities.get,
    )

    assert resolved == ("sha256:first", "sha256:second")


def test_binding_references_allow_optional_absence() -> None:
    plan = CompiledBindingPlan(
        field="previous",
        artifact_identifier="example.document.v1",
        minimum=0,
        maximum=1,
        references=("node:previous.output",),
    )

    assert resolve_binding_references(plan, lambda reference: None) == ()


@pytest.mark.parametrize(
    ("minimum", "maximum", "identities", "message"),
    [
        (2, None, {"input:first": "sha256:first"}, "at least 2"),
        (
            0,
            1,
            {
                "input:first": "sha256:first",
                "input:second": "sha256:second",
            },
            "at most 1",
        ),
    ],
)
def test_binding_references_enforce_frozen_cardinality(
    minimum: int,
    maximum: int | None,
    identities: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(BindingResolutionError, match=message):
        resolve_binding_references(
            _binding_plan(minimum=minimum, maximum=maximum),
            identities.get,
        )


def _materialized(path: Path, cleanup: MaterializationCleanup) -> MaterializedArtifact:
    return cast(
        MaterializedArtifact,
        SimpleNamespace(path=path, cleanup=cleanup),
    )


def test_attempt_materializations_remove_only_caller_owned_paths(
    tmp_path: Path,
) -> None:
    owned = tmp_path / "owned.pa"
    protected = tmp_path / "protected.pa"
    owned.touch()
    protected.touch()
    materializations = AttemptMaterializations()

    assert (
        materializations.track(_materialized(owned, MaterializationCleanup.REQUIRED))
        == owned
    )
    assert (
        materializations.track(
            _materialized(protected, MaterializationCleanup.NOT_REQUIRED)
        )
        == protected
    )

    materializations.close()
    materializations.close()

    assert not owned.exists()
    assert protected.exists()
    with pytest.raises(RuntimeError, match="closed"):
        materializations.track(_materialized(owned, MaterializationCleanup.REQUIRED))


def test_attempt_materializations_clean_all_owned_paths_after_failure() -> None:
    removed: list[Path] = []

    def remove(path: Path) -> None:
        removed.append(path)
        if path.name in {"first.pa", "second.pa"}:
            raise OSError(f"cleanup failed: {path.name}")

    materializations = AttemptMaterializations(remove=remove)
    for name in ("first.pa", "second.pa", "third.pa"):
        materializations.track(
            _materialized(Path(name), MaterializationCleanup.REQUIRED)
        )

    with pytest.raises(OSError, match="cleanup failed"):
        materializations.close()
    materializations.close()

    assert removed == [Path("third.pa"), Path("second.pa"), Path("first.pa")]


def _stored(identity: str) -> StoredArtifact:
    descriptor = cast(
        ManagedArtifactDescriptor,
        SimpleNamespace(identity=identity),
    )
    location = cast(ArtifactLocation, SimpleNamespace(identity=identity))
    return StoredArtifact(descriptor=descriptor, locations=(location,))


class _MaterializingStore:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.calls: list[tuple[str, Path]] = []
        self.fail_at = fail_at

    def materialize(
        self,
        *,
        descriptor: ManagedArtifactDescriptor,
        locations: tuple[ArtifactLocation, ...],
        destination: Path,
    ) -> MaterializedArtifact:
        del locations
        self.calls.append((descriptor.identity, destination))
        if self.fail_at == len(self.calls):
            raise OSError("materialization failed")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch()
        return _materialized(destination, MaterializationCleanup.REQUIRED)


def test_materialize_binding_inputs_preserves_identity_order(tmp_path: Path) -> None:
    store = _MaterializingStore()
    ownership = AttemptMaterializations()
    artifacts = {
        "sha256:first": _stored("sha256:first"),
        "sha256:second": _stored("sha256:second"),
    }

    paths = materialize_binding_inputs(
        _binding_plan(minimum=1, maximum=None),
        ("sha256:first", "sha256:second"),
        artifacts,
        cast(ArtifactStore, store),
        tmp_path / "attempt",
        ownership,
    )

    assert paths == (
        tmp_path / "attempt" / "documents-0000.pa",
        tmp_path / "attempt" / "documents-0001.pa",
    )
    assert [identity for identity, _ in store.calls] == [
        "sha256:first",
        "sha256:second",
    ]
    ownership.close()
    assert not any(path.exists() for path in paths)


def test_materialize_binding_inputs_cleanup_survives_partial_failure(
    tmp_path: Path,
) -> None:
    store = _MaterializingStore(fail_at=2)
    ownership = AttemptMaterializations()
    artifacts = {
        "sha256:first": _stored("sha256:first"),
        "sha256:second": _stored("sha256:second"),
    }

    with pytest.raises(OSError, match="materialization failed"):
        materialize_binding_inputs(
            _binding_plan(minimum=1, maximum=None),
            ("sha256:first", "sha256:second"),
            artifacts,
            cast(ArtifactStore, store),
            tmp_path / "attempt",
            ownership,
        )
    ownership.close()

    assert not (tmp_path / "attempt" / "documents-0000.pa").exists()


class _Definition:
    def __init__(self, artifact: object) -> None:
        self.artifact = artifact
        self.resolve_count = 0

    def resolve(self) -> object:
        self.resolve_count += 1
        return self.artifact


class _Catalogs:
    def __init__(self, definition: _Definition) -> None:
        self.definition = definition
        self.identifiers: list[str] = []

    def resolve(self, identifier: str) -> _Definition:
        self.identifiers.append(identifier)
        return self.definition


def test_build_read_binding_value_preserves_repeated_shape_and_order() -> None:
    artifact = object()
    definition = _Definition(artifact)
    catalogs = cast(ArtifactCatalogCollection, _Catalogs(definition))
    paths = (Path("first.pa"), Path("second.pa"))

    value = build_read_binding_value(
        _binding_plan(minimum=1, maximum=None),
        paths,
        catalogs,
        binding_factory=lambda resolved, path: (resolved, path),
    )

    assert value == ((artifact, paths[0]), (artifact, paths[1]))
    assert definition.resolve_count == 1


def test_build_read_binding_value_uses_core_binding_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import provium_pipeline.task_executor as task_executor_module

    artifact = object()
    catalogs = cast(ArtifactCatalogCollection, _Catalogs(_Definition(artifact)))

    def binding(resolved: object, path: Path) -> tuple[object, Path]:
        return resolved, path

    monkeypatch.setattr(task_executor_module, "ArtifactReadBinding", binding)

    assert build_read_binding_value(
        _binding_plan(minimum=1, maximum=1),
        (Path("document.pa"),),
        catalogs,
    ) == (artifact, Path("document.pa"))


def test_build_read_binding_value_supports_optional_and_required_scalars() -> None:
    artifact = object()
    catalogs = cast(ArtifactCatalogCollection, _Catalogs(_Definition(artifact)))
    optional = CompiledBindingPlan(
        field="previous",
        artifact_identifier="example.document.v1",
        minimum=0,
        maximum=1,
        references=(),
    )
    required = replace(optional, field="document", minimum=1)

    assert (
        build_read_binding_value(
            optional,
            (),
            catalogs,
            binding_factory=lambda resolved, path: (resolved, path),
        )
        is None
    )
    assert build_read_binding_value(
        required,
        (Path("document.pa"),),
        catalogs,
        binding_factory=lambda resolved, path: (resolved, path),
    ) == (artifact, Path("document.pa"))


def _output_metadata() -> ProcedureContractMetadata:
    fields = tuple(
        ProcedureIOFieldMetadata(
            name=name,
            artifact_identifier="example.document.v1",
            artifact_description="Document",
            direction="output",
            minimum=minimum,
            maximum=1,
            repeated=False,
            description=None,
        )
        for name, minimum in (("required", 1), ("optional", 0))
    )
    return ProcedureContractMetadata(
        configuration_target=None,
        _configuration_schema_json=None,
        configuration_schema_digest=None,
        setup_inputs=(),
        inputs=(),
        outputs=fields,
        digest="sha256:contract",
    )


def test_build_output_bindings_includes_required_and_optional_outputs(
    tmp_path: Path,
) -> None:
    artifact = object()
    catalogs = cast(ArtifactCatalogCollection, _Catalogs(_Definition(artifact)))

    bindings = build_output_bindings(
        ("required", "optional"),
        _output_metadata(),
        catalogs,
        tmp_path,
        binding_factory=lambda resolved, path: (resolved, path),
    )

    assert bindings == {
        "required": (artifact, tmp_path / "required.pa"),
        "optional": (artifact, tmp_path / "optional.pa"),
    }


def test_build_output_bindings_uses_core_binding_constructor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import provium_pipeline.task_executor as task_executor_module

    artifact = object()
    catalogs = cast(ArtifactCatalogCollection, _Catalogs(_Definition(artifact)))

    def binding(resolved: object, path: Path) -> tuple[object, Path]:
        return resolved, path

    monkeypatch.setattr(task_executor_module, "ArtifactWriteBinding", binding)

    assert build_output_bindings(
        ("required", "optional"),
        _output_metadata(),
        catalogs,
        tmp_path,
    )["required"] == (artifact, tmp_path / "required.pa")


def test_build_output_bindings_rejects_frozen_contract_drift(tmp_path: Path) -> None:
    catalogs = cast(ArtifactCatalogCollection, _Catalogs(_Definition(object())))

    with pytest.raises(BindingResolutionError, match="output fields"):
        build_output_bindings(
            ("required",),
            _output_metadata(),
            catalogs,
            tmp_path,
            binding_factory=lambda resolved, path: (resolved, path),
        )


def test_verify_procedure_contract_accepts_the_frozen_digest() -> None:
    metadata = _output_metadata()

    class _Contract:
        metadata = _output_metadata()

    class _Definition:
        def resolve_contract(self) -> type[_Contract]:
            return _Contract

    definition = cast(ProcedureDefinition[Any], _Definition())

    assert verify_procedure_contract(definition, metadata.digest) is _Contract


def test_verify_procedure_contract_rejects_installed_drift() -> None:
    class _Contract:
        metadata = _output_metadata()

    class _Definition:
        def resolve_contract(self) -> type[_Contract]:
            return _Contract

    definition = cast(ProcedureDefinition[Any], _Definition())

    with pytest.raises(ProcedureContractMismatchError, match="digest"):
        verify_procedure_contract(definition, "sha256:frozen")


def test_materialize_binding_inputs_rejects_unknown_identity(tmp_path: Path) -> None:
    with pytest.raises(BindingResolutionError, match="not available"):
        materialize_binding_inputs(
            _binding_plan(minimum=1, maximum=None),
            ("sha256:missing",),
            {},
            cast(ArtifactStore, _MaterializingStore()),
            tmp_path / "attempt",
            AttemptMaterializations(),
        )
