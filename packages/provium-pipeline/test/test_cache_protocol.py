from typing import get_type_hints

from pipeline_fixtures import COPY
from test_store import make_artifact
from test_target_planning import graph

from provium_pipeline.requests import ExecutionRequest
from provium_pipeline.runtime import ExecutionContext
from provium_pipeline.store import ArtifactStore


def test_engine_accepts_cache_protocol(tmp_path):
    from provium_pipeline.store import ArtifactCache

    with ArtifactStore(str(tmp_path / "cache")) as store:
        assert isinstance(store, ArtifactCache)
        assert get_type_hints(ExecutionContext.__init__)["store"] is ArtifactCache


def test_custom_manifest_storage_reuses_and_publishes(tmp_path):
    class MemoryResults:
        def __init__(self, files):
            self.namespace = "memory://results"
            self.root = files.root
            self.scratch = files.scratch
            self.writer_lock = files.writer_lock
            self.inspect = files.inspect
            self.stage = files.stage
            self.restore = files.restore
            self.materialize = files.materialize
            self.verify = files.verify
            self.results = {}

        def commit(self, computation, outputs):
            self.results[computation] = outputs

        def manifest(self, computation):
            return self.results.get(computation)

    source = make_artifact(tmp_path / "input.pa", "hello")
    with ArtifactStore(str(tmp_path / "cache")) as files:
        cache = MemoryResults(files)
        for attempt in range(2):
            destination = tmp_path / f"output-{attempt}.pa"
            with ExecutionContext(
                graph(), cache, procedures={COPY.identifier: COPY}
            ) as execution:
                execution.start(
                    [
                        ExecutionRequest(
                            {"left": str(source)}, {"left": str(destination)}
                        )
                    ]
                )
            assert execution.error is None
            assert execution.outcomes[0].state == "completed"
            assert execution.worker_starts == (1 if attempt == 0 else 0)
            assert files.inspect(str(destination)).fingerprint == (
                execution.outcomes[0].outputs["left"].fingerprint
            )
        assert len(cache.results) == 1
        assert not (files.root / "results").exists()
