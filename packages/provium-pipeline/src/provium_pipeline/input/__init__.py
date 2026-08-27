"""Input records, immutable input sets, resolution, and validation."""

from .codec import InputRecordDecodeError, load_input_records_ndjson
from .models import (
    InputRecord,
    InputSet,
    InputSourceDescriptor,
    InputSourceKind,
    RunInputSnapshot,
)
from .resolver import (
    INPUT_RECORD_RESOLVER_ENTRY_POINT_GROUP,
    InputRecordResolver,
    InputRecordResolverCatalog,
    InputResolutionContext,
    ResolveInputRecordsRequest,
    discover_input_record_resolvers,
    resolve_input_snapshot,
)
from .sqlite import SQLiteInputSetStore
from .validation import InputValidationError, validate_input_snapshot

__all__ = [
    "INPUT_RECORD_RESOLVER_ENTRY_POINT_GROUP",
    "InputRecord",
    "InputRecordDecodeError",
    "InputRecordResolver",
    "InputRecordResolverCatalog",
    "InputResolutionContext",
    "InputSet",
    "InputSourceDescriptor",
    "InputSourceKind",
    "InputValidationError",
    "ResolveInputRecordsRequest",
    "RunInputSnapshot",
    "SQLiteInputSetStore",
    "discover_input_record_resolvers",
    "load_input_records_ndjson",
    "resolve_input_snapshot",
    "validate_input_snapshot",
]
