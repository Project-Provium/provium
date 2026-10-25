"""Persistence contracts and explicit backend selection."""

from provium_dispatch.persistence.factory import Backend, open_backend
from provium_dispatch.persistence.interface import (
    Lease,
    PersistenceBackend,
    QueueBackend,
)

__all__ = ["Backend", "Lease", "PersistenceBackend", "QueueBackend", "open_backend"]
