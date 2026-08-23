"""Compatibility negotiation with the installed Provium core."""

from __future__ import annotations

import provium

SUPPORTED_CORE_API_VERSION = 1
_INSTALLED_CORE_API_VERSION = getattr(provium, "PROVIUM_CORE_API_VERSION", None)


class IncompatibleCoreError(RuntimeError):
    """Raised when the installed Provium core contract is unsupported."""


def require_compatible_core(
    *, core_api_version: object = _INSTALLED_CORE_API_VERSION
) -> None:
    """Raise one actionable diagnostic when the core API is unsupported."""

    if core_api_version is None:
        raise IncompatibleCoreError(
            "provium-pipeline requires Provium core API version "
            f"{SUPPORTED_CORE_API_VERSION}; installed Provium does not expose "
            "PROVIUM_CORE_API_VERSION"
        )
    if core_api_version != SUPPORTED_CORE_API_VERSION:
        raise IncompatibleCoreError(
            "provium-pipeline requires Provium core API version "
            f"{SUPPORTED_CORE_API_VERSION}; installed Provium exposes version "
            f"{core_api_version}"
        )


__all__ = [
    "IncompatibleCoreError",
    "SUPPORTED_CORE_API_VERSION",
    "require_compatible_core",
]
