from __future__ import annotations

from dataclasses import dataclass

import pytest

from provium_pipeline.task_executor import PreparedProcedureCache, PreparedProcedureKey


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
