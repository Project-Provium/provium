import os

import pytest
from test_store import make_artifact

from provium_pipeline.store import ArtifactStore


def cached(store, source, key):
    artifact = store.inspect(str(source))
    outputs = store.stage(
        key, {"result": str(source)}, {"result": artifact.fingerprint["provenance"]}
    )
    store.commit(key, outputs)
    return outputs


def test_clear_filters_entries_and_preserves_publications(tmp_path):
    from provium_pipeline.cache import clear_cache

    source = make_artifact(tmp_path / "source.pa")
    with ArtifactStore(str(tmp_path / "cache")) as store:
        first = cached(store, source, "a" * 64)
        second = cached(store, source, "b" * 64)
        result = clear_cache(store, keys={"a" * 64})
        assert result.removed == ("a" * 64,)
        assert result.bytes_removed > 0
        assert store.manifest("a" * 64) is None
        assert store.manifest("b" * 64) == second
        assert source.exists()
        assert not store.fetch(first["result"]["location"]).exists()


def test_clear_refuses_paths_outside_cache(tmp_path):
    from provium_pipeline.cache import clear_cache

    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / ("a" * 64)
    protected.mkdir()
    (protected / "keep").write_text("keep")
    with ArtifactStore(str(tmp_path / "cache")) as store:
        (store.root / "artifacts").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match="outside"):
            clear_cache(store)
        assert (protected / "keep").read_text() == "keep"


def test_custom_inventory_forgets_metadata_before_removing_bytes():
    from contextlib import nullcontext

    from provium_pipeline.cache import CacheEntry, clear_cache

    events = []

    class Inventory:
        namespace = "memory://cache"

        def guard(self):
            return nullcontext()

        def entries(self):
            return [CacheEntry("a", 12, 0)]

        def remove(self, key):
            events.append(("remove", key))

    class Guard:
        def hold(self, namespace):
            return nullcontext()

        def forget(self, namespace, keys):
            events.append(("forget", list(keys)))

    assert clear_cache(Inventory(), guard=Guard()).bytes_removed == 12
    assert events == [("forget", ["a"]), ("remove", "a")]


def test_maintenance_validation_and_orphans(tmp_path):
    from provium_pipeline.cache import FileCache, clear_cache

    with ArtifactStore(str(tmp_path / "cache")) as store:
        inventory = FileCache(store)
        with pytest.raises(ValueError, match="key"):
            inventory.remove("../outside")
        with pytest.raises(ValueError, match="finite"):
            clear_cache(store, older_than=float("nan"))
        orphan = store.root / "artifacts" / ("c" * 64)
        orphan.mkdir(parents=True)
        assert clear_cache(store, max_bytes=0).removed == ()
        assert clear_cache(store).removed == ("c" * 64,)
        result = store.root / "results" / ("d" * 64 + ".json")
        result.parent.mkdir()
        result.write_text("{}")
        assert clear_cache(store).removed == ("d" * 64,)
        (store.root / ".dispatch-cache").touch()
        with pytest.raises(ValueError, match="dispatch"):
            clear_cache(store)
        store.root = None
        with pytest.raises(ValueError, match="local"):
            FileCache(store)


def test_cache_clear_command(tmp_path, capsys):
    import argparse

    from provium_pipeline.cli.commands.cache import ClearCommand

    parser = argparse.ArgumentParser()
    command = ClearCommand()
    command.configure(parser)
    args = parser.parse_args(["--store", str(tmp_path / "cache"), "--json"])
    assert command.execute(args) == 0
    assert '"bytes_removed": 0' in capsys.readouterr().out
    args.json = False
    assert command.execute(args) == 0
    assert "Removed 0" in capsys.readouterr().out
    args.max_bytes = -1
    assert command.execute(args) == 2


def test_discard_only_published_run_computations(tmp_path):
    from pipeline_fixtures import COPY
    from test_target_planning import graph

    from provium_pipeline.cache import discard_published_cache
    from provium_pipeline.requests import ExecutionRequest
    from provium_pipeline.runtime import ExecutionContext

    source = make_artifact(tmp_path / "source.pa")
    destination = tmp_path / "published.pa"
    with ArtifactStore(str(tmp_path / "cache")) as store:
        cached(store, source, "f" * 64)
        with ExecutionContext(
            graph(), store, procedures={COPY.identifier: COPY}
        ) as execution:
            execution.start(
                [ExecutionRequest({"left": str(source)}, {"left": str(destination)})]
            )
        with store.writer_lock(), pytest.warns(RuntimeWarning, match="retained"):
            assert (
                discard_published_cache(
                    store, execution.outcomes, execution.computation_keys
                )
                is None
            )
        from dataclasses import replace

        with pytest.warns(RuntimeWarning, match="not every item"):
            assert (
                discard_published_cache(
                    store,
                    [replace(execution.outcomes[0], state="failed")],
                    execution.computation_keys,
                )
                is None
            )
        removed = discard_published_cache(
            store, execution.outcomes, execution.computation_keys
        )
        assert len(removed.removed) == 1
        assert store.manifest("f" * 64) is not None
        assert (
            store.inspect(str(destination)).fingerprint
            == execution.outcomes[0].outputs["left"].fingerprint
        )


def test_out_of_space_preserves_exception_and_suggests_unused_cache(
    tmp_path, monkeypatch
):
    import errno

    source = make_artifact(tmp_path / "source.pa")
    with ArtifactStore(str(tmp_path / "cache")) as store:
        artifact = store.inspect(str(source))
        cached(store, source, "a" * 64)

        def full(*args):
            raise OSError(errno.ENOSPC, "disk full")

        monkeypatch.setattr(store, "_put", full)
        with (
            pytest.warns(RuntimeWarning, match="unused cached"),
            pytest.raises(OSError) as error,
        ):
            store.stage(
                "b" * 64,
                {"result": str(source)},
                {"result": artifact.fingerprint["provenance"]},
            )
        assert error.value.errno == errno.ENOSPC


@pytest.mark.parametrize("mode", ["empty", "remote", "permission"])
def test_space_hint_is_only_for_local_retained_artifacts(tmp_path, monkeypatch, mode):
    import errno
    import warnings

    with ArtifactStore(str(tmp_path / "cache")) as store:
        code = errno.EACCES if mode == "permission" else errno.ENOSPC
        if mode == "remote":
            store.root = None

        def fail(*args):
            raise OSError(code, "storage failure")

        monkeypatch.setattr(store, "_put", fail)
        with warnings.catch_warnings(record=True) as seen, pytest.raises(OSError):
            store._write("unused", tmp_path / "source")
        assert not seen


def test_input_only_outcomes_keep_cache(tmp_path):
    from provium_pipeline.cache import discard_published_cache
    from provium_pipeline.requests import InputSet
    from provium_pipeline.runtime import Outcome

    with ArtifactStore(str(tmp_path / "cache")) as store:
        with pytest.warns(RuntimeWarning, match="retained"):
            assert (
                discard_published_cache(
                    store, [Outcome(InputSet({}), "completed")], set()
                )
                is None
            )


def test_clear_never_overlaps_execution(tmp_path):
    from provium_pipeline.cache import clear_cache

    with ArtifactStore(str(tmp_path / "cache")) as store:
        with store.writer_lock(), pytest.raises(RuntimeError, match="writer"):
            clear_cache(store)
        assert clear_cache(store).removed == ()


def test_shared_reader_prevents_deletion(tmp_path):
    from provium_pipeline.cache import clear_cache

    with ArtifactStore(str(tmp_path / "cache")) as store:
        with store.writer_lock(shared=True), store.writer_lock(shared=True):
            with pytest.raises(RuntimeError):
                clear_cache(store)


def test_trim_oldest_entries_and_age_filter(tmp_path):
    from provium_pipeline.cache import clear_cache

    source = make_artifact(tmp_path / "source.pa")
    with ArtifactStore(str(tmp_path / "cache")) as store:
        cached(store, source, "a" * 64)
        cached(store, source, "b" * 64)
        os.utime(store.root / "results" / ("a" * 64 + ".json"), (1, 1))
        assert clear_cache(store, older_than=100).removed == ("a" * 64,)
        assert clear_cache(store, max_bytes=0).removed == ("b" * 64,)
        with pytest.raises(ValueError):
            clear_cache(store, max_bytes=-1)
        with pytest.raises(ValueError):
            clear_cache(store, older_than=-1)
