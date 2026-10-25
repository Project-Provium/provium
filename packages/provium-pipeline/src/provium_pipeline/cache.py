"""Cache maintenance independent of execution and durable queue implementations."""

from __future__ import annotations

import math
import re
import shutil
import time
import warnings
from collections.abc import Collection, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from provium_pipeline.store import ArtifactStore

if TYPE_CHECKING:
    from provium_pipeline.runtime import Outcome


@dataclass(frozen=True)
class CacheEntry:
    key: str
    size: int
    modified: float


@dataclass(frozen=True)
class CacheClearResult:
    removed: tuple[str, ...]
    bytes_removed: int
    bytes_remaining: int


class MaintenanceGuard(Protocol):
    """Exclude admission/readers and recheck authoritative queued dependencies.

    A guard must refuse maintenance while any protected dependency exists.
    Forgetting metadata must be durable before artifact bytes are removed.
    """

    def hold(self, namespace: str) -> AbstractContextManager[None]: ...

    def forget(self, namespace: str, keys: Sequence[str]) -> None: ...


class CacheInventory(Protocol):
    namespace: str

    def guard(self) -> AbstractContextManager[None]: ...
    def entries(self) -> list[CacheEntry]: ...
    def remove(self, key: str) -> None: ...


class FileCache:
    """Inventory and deletion of local computation directories only."""

    def __init__(self, store: ArtifactStore) -> None:
        if store.root is None:
            raise ValueError("file cache maintenance requires a local store")
        self.store = store
        self.root = store.root
        self.namespace = store.namespace

    def guard(self) -> AbstractContextManager[None]:
        return self.store.writer_lock()

    def entries(self) -> list[CacheEntry]:
        for directory in ("artifacts", "results"):
            self._assert_path_is_contained(self.root / directory)
        keys = {
            path.stem
            for directory in ("artifacts", "results")
            for path in (self.root / directory).glob("*")
            if re.fullmatch("[0-9a-f]{64}", path.stem)
        }
        entries: list[CacheEntry] = []
        for key in keys:
            directory = self.root / "artifacts" / key
            manifest = self.root / "results" / f"{key}.json"
            self._assert_path_is_contained(directory)
            self._assert_path_is_contained(manifest)
            paths = list(directory.rglob("*")) + [manifest]
            files = [path for path in paths if path.is_file()]
            size = sum(path.stat().st_size for path in files)
            modified = (
                manifest.stat().st_mtime
                if manifest.exists()
                else max((path.stat().st_mtime for path in files), default=0)
            )
            entries.append(CacheEntry(key, size, modified))
        return entries

    def _assert_path_is_contained(self, path: Path) -> None:
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("cache path points outside the cache")

    def remove(self, key: str) -> None:
        if not re.fullmatch("[0-9a-f]{64}", key):
            raise ValueError("invalid cache key")
        manifest = self.root / "results" / f"{key}.json"
        directory = self.root / "artifacts" / key
        self._assert_path_is_contained(manifest)
        self._assert_path_is_contained(directory)
        manifest.unlink(missing_ok=True)
        if directory.exists():
            shutil.rmtree(directory)


def discard_published_cache(
    store: ArtifactStore,
    outcomes: Sequence[Outcome],
    keys: Collection[str],
    *,
    guard: MaintenanceGuard | None = None,
) -> CacheClearResult | None:
    """Discard only this run's cache after every requested publication succeeded."""
    if any(
        outcome.state != "completed" or outcome.input_set.output_destinations is None
        for outcome in outcomes
    ):
        warnings.warn(
            "Cache retained: not every item has successfully published destinations.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None
    try:
        return clear_cache(store, keys=keys, guard=guard)
    except RuntimeError as error:
        warnings.warn(f"Cache retained: {error}", RuntimeWarning, stacklevel=2)
        return None


def clear_cache(
    cache: CacheInventory | ArtifactStore,
    *,
    keys: Collection[str] | None = None,
    older_than: float = 0,
    max_bytes: int | None = None,
    guard: MaintenanceGuard | None = None,
) -> CacheClearResult:
    """Clear selected entries, or trim oldest entries to a byte budget.

    The guard is acquired before selection. There is no deferred deletion:
    reader admission remains possible until this exclusive deletion boundary.
    """
    if older_than < 0 or not math.isfinite(older_than):
        raise ValueError("cache age must be finite and nonnegative")
    if max_bytes is not None and max_bytes < 0:
        raise ValueError("cache byte limit must be nonnegative")
    if isinstance(cache, ArtifactStore):
        cache = FileCache(cache)
    with cache.guard(), guard.hold(cache.namespace) if guard else nullcontext():
        if (
            guard is None
            and isinstance(cache, FileCache)
            and (cache.root / ".dispatch-cache").exists()
        ):
            raise ValueError(
                "dispatch cache requires a maintenance guard; use dispatch cache clear"
            )
        entries = sorted(cache.entries(), key=lambda entry: (entry.modified, entry.key))
        remaining = sum(entry.size for entry in entries)
        selected: list[CacheEntry] = []
        cutoff = time.time() - older_than
        for entry in entries:
            if max_bytes is not None and remaining <= max_bytes:
                break
            if (keys is None or entry.key in keys) and entry.modified <= cutoff:
                selected.append(entry)
                remaining -= entry.size
        selected_keys = [entry.key for entry in selected]
        if guard is not None:
            guard.forget(cache.namespace, selected_keys)
        for entry in selected:
            cache.remove(entry.key)
        return CacheClearResult(
            tuple(selected_keys), sum(entry.size for entry in selected), remaining
        )
