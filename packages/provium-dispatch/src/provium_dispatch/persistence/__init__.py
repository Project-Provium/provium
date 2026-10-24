"""Persistence contracts and explicit backend selection."""

from .factory import Backend, open_backend
from .interface import Lease, PersistenceBackend, QueueBackend

__all__ = ["Backend", "Lease", "PersistenceBackend", "QueueBackend", "open_backend"]
