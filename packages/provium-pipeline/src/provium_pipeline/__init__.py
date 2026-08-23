"""Typed, reproducible, idempotent local pipelines for Provium."""

from importlib.metadata import version

from .compatibility import (
    SUPPORTED_CORE_API_VERSION,
    IncompatibleCoreError,
    require_compatible_core,
)

__version__ = version("provium-pipeline")

require_compatible_core()

__all__ = [
    "IncompatibleCoreError",
    "SUPPORTED_CORE_API_VERSION",
    "__version__",
    "require_compatible_core",
]
