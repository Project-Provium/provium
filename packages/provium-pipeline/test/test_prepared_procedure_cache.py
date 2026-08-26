from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from provium.procedure.config import ConfigurationSnapshot, ProcedureConfig
from provium_pipeline.task_executor import (
    ConfigurationSnapshotMismatchError,
    PreparedProcedureCache,
    PreparedProcedureKey,
    verify_configuration_snapshot,
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
