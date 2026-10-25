"""PostgreSQL registry, deduplicated inputs, and fenced computation leases."""

from __future__ import annotations

import tempfile
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
from provium.semantic import canonical_hash
from provium_pipeline.models import PipelineConfiguration
from provium_pipeline.models import PipelineDefinition as BasicDefinition
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from provium_dispatch.models import (
    ComputeProfile,
    PipelineDefinition,
    definition_snapshot,
)
from provium_dispatch.persistence.interface import Lease
from provium_dispatch.persistence.queues import assert_queue_name_is_valid

MIGRATIONS = [
    """
    CREATE TABLE provium_registry (
        kind text NOT NULL, name text NOT NULL, document jsonb NOT NULL,
        PRIMARY KEY(kind, name)
    );
    CREATE TABLE provium_inputs (
        id bigserial PRIMARY KEY, pipeline text NOT NULL,
        snapshot jsonb NOT NULL, inputs jsonb NOT NULL, dedup text NOT NULL UNIQUE,
        state text NOT NULL DEFAULT 'pending', detail jsonb NOT NULL DEFAULT '{}'
    );
    CREATE INDEX ON provium_inputs(pipeline, state, id);
    CREATE TABLE provium_computations (
        namespace text NOT NULL, key text NOT NULL,
        generation bigint NOT NULL DEFAULT 0, owner text, run text,
        expires timestamptz, result jsonb, state text NOT NULL DEFAULT 'available',
        PRIMARY KEY(namespace, key)
    );
    CREATE TABLE provium_failures (
        namespace text NOT NULL, key text NOT NULL, run text NOT NULL,
        error text NOT NULL,
        PRIMARY KEY(namespace, key, run)
    );
    """,
    """
    CREATE TABLE provium_definitions(name text PRIMARY KEY, document jsonb NOT NULL);
    ALTER TABLE provium_inputs ADD COLUMN definition jsonb;
    """,
    """
    ALTER TABLE provium_inputs ADD COLUMN queue_group text NOT NULL DEFAULT 'default';
    ALTER TABLE provium_inputs DROP CONSTRAINT provium_inputs_dedup_key;
    ALTER TABLE provium_inputs ADD UNIQUE(queue_group, dedup);
    CREATE INDEX ON provium_inputs(pipeline, queue_group, state, id);
    """,
    """
    CREATE TABLE provium_input_sets (
        hash text PRIMARY KEY, inputs jsonb NOT NULL
    );
    CREATE TABLE provium_input_set_names (
        name text PRIMARY KEY, hash text NOT NULL REFERENCES provium_input_sets(hash)
    );
    CREATE INDEX ON provium_input_set_names(hash);
    """,
    """
    ALTER TABLE provium_inputs ADD COLUMN destinations jsonb;
    """,
]


class PostgresBackend:
    def __init__(self, connection: str) -> None:
        self.connection = connection
        self.migrate()

    @contextmanager
    def reader(self, namespace: str) -> Generator[None]:
        """Protect all artifacts consumed by one execution across its lifetime."""
        with self.connect() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock_shared(hashtext(%s), 83451)",
                (namespace,),
            )
            yield

    @contextmanager
    def hold(self, namespace: str) -> Generator[None]:
        """Exclude admission and readers before checking the durable queue."""
        with self.connect() as connection:
            admission = connection.execute(
                "SELECT pg_try_advisory_xact_lock(742819407) AS acquired"
            ).fetchone()
            readers = connection.execute(
                "SELECT pg_try_advisory_xact_lock(hashtext(%s), 83451) AS acquired",
                (namespace,),
            ).fetchone()
            if not admission["acquired"] or not readers["acquired"]:
                raise RuntimeError("cache is protected by active work")
            queued = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM provium_inputs "
                "WHERE state IN ('pending','partial','cancelled')) AS protected"
            ).fetchone()
            if queued["protected"]:
                raise RuntimeError("cache is protected by queued work")
            active = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM provium_computations "
                "WHERE namespace=%s AND state='running' "
                "AND expires>clock_timestamp()) AS protected",
                (namespace,),
            ).fetchone()
            if active["protected"]:
                raise RuntimeError("cache is protected by active computations")
            yield

    def forget(self, namespace: str, keys: Sequence[str]) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE provium_computations SET result=NULL,state='available' "
                "WHERE namespace=%s AND key=ANY(%s)",
                (namespace, list(keys)),
            )

    def connect(self) -> Any:
        return psycopg.Connection[dict[str, Any]].connect(
            self.connection, row_factory=dict_row
        )

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(742819405)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provium_migrations "
                "(version integer PRIMARY KEY)"
            )
            versions = [
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM provium_migrations ORDER BY version"
                )
            ]
            if versions != list(range(1, len(versions) + 1)) or len(versions) > len(
                MIGRATIONS
            ):
                raise ValueError("unsupported database migration versions")
            for index in range(len(versions), len(MIGRATIONS)):
                connection.execute(MIGRATIONS[index])
                connection.execute(
                    "INSERT INTO provium_migrations VALUES (%s)", (index + 1,)
                )

    def register_input_set(self, name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        from provium_pipeline.input_sets import (
            assert_input_set_name_is_valid,
            input_set_hash,
        )

        assert_input_set_name_is_valid(name)
        key = input_set_hash(inputs)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO provium_input_sets VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (key, Jsonb(inputs)),
            )
            connection.execute(
                "INSERT INTO provium_input_set_names VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (name, key),
            )
            row = connection.execute(
                "SELECT hash FROM provium_input_set_names WHERE name = %s FOR UPDATE",
                (name,),
            ).fetchone()
            if row["hash"] != key:
                raise ValueError(
                    f"input-set name already registered to different inputs: {name}"
                )
            record = connection.execute(
                "SELECT hash, inputs FROM provium_input_sets WHERE hash = %s", (key,)
            ).fetchone()
        return record

    def resolve_input_set(
        self, *, name: str | None = None, key: str | None = None
    ) -> dict[str, Any]:
        from provium_pipeline.input_sets import assert_input_set_selector_is_valid

        assert_input_set_selector_is_valid(name, key)
        with self.connect() as connection:
            if name is not None:
                row = connection.execute(
                    "SELECT s.hash, s.inputs FROM provium_input_sets s "
                    "JOIN provium_input_set_names n ON n.hash = s.hash "
                    "WHERE n.name = %s",
                    (name,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT hash, inputs FROM provium_input_sets WHERE hash = %s",
                    (key,),
                ).fetchone()
        if row is None:
            raise ValueError(f"unknown input set: {name if name is not None else key}")
        return row

    def find_input_sets(self, key: str) -> list[str]:
        with self.connect() as connection:
            return [
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM provium_input_set_names "
                    "WHERE hash = %s ORDER BY name",
                    (key,),
                )
            ]

    def unregister_input_sets(
        self, *, name: str | None = None, key: str | None = None
    ) -> list[str]:
        from provium_pipeline.input_sets import assert_input_set_selector_is_valid

        assert_input_set_selector_is_valid(name, key)
        with self.connect() as connection:
            if name is not None:
                rows = connection.execute(
                    "DELETE FROM provium_input_set_names "
                    "WHERE name = %s RETURNING name",
                    (name,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "DELETE FROM provium_input_set_names "
                    "WHERE hash = %s RETURNING name",
                    (key,),
                ).fetchall()
        return sorted(row["name"] for row in rows)

    def register(
        self,
        documents: list[dict[str, Any]],
        *,
        definitions: dict[str, BasicDefinition] | None = None,
    ) -> None:
        definitions = definitions or {}
        normalized: list[dict[str, Any]] = []
        for doc in documents:
            if doc.get("kind") == "PipelineConfiguration":
                graph = PipelineConfiguration.model_validate(doc)
                from provium_pipeline.planning import build_plan

                build_plan(graph, definitions.get(graph.name))
                normalized.append(graph.model_dump(mode="json"))
            elif doc.get("kind") == "ComputeProfile":
                normalized.append(
                    ComputeProfile.model_validate(doc).model_dump(mode="json")
                )
            else:
                raise ValueError("unknown registry document kind")
        documents = normalized
        keys = [(doc["kind"], doc["name"]) for doc in documents]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate registry objects")
        with self.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(742819406)")
            for name, definition in definitions.items():
                payload = definition_snapshot(definition)
                connection.execute(
                    "INSERT INTO provium_definitions VALUES(%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    (name, Jsonb(payload)),
                )
                existing = connection.execute(
                    "SELECT document FROM provium_definitions WHERE name=%s FOR UPDATE",
                    (name,),
                ).fetchone()
                if existing["document"] != payload:
                    raise ValueError(f"registered definition differs: {name}")
            for document in documents:
                key = (document["kind"], document["name"])
                connection.execute(
                    "INSERT INTO provium_registry VALUES (%s,%s,%s) ON "
                    "CONFLICT DO NOTHING",
                    (*key, Jsonb(document)),
                )
                row = connection.execute(
                    "SELECT document FROM provium_registry WHERE kind=%s "
                    "AND name=%s FOR UPDATE",
                    key,
                ).fetchone()
                if row["document"] != document:
                    raise ValueError(f"registered object differs: {key}")

    def registered(self, kind: str | None = None) -> list[dict[str, Any]]:
        """List registered object identities without loading procedure catalogs."""
        if kind not in (None, "PipelineConfiguration", "ComputeProfile"):
            raise ValueError("unknown registry document kind")
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT kind,name FROM provium_registry "
                    "WHERE (%s::text IS NULL OR kind=%s) ORDER BY kind,name",
                    (kind, kind),
                )
            )

    def queue_info(
        self, pipeline: str | None = None, *, queue_group: str = "default"
    ) -> list[dict[str, Any]]:
        """Summarize states in one queue, including empty pipeline queues."""
        assert_queue_name_is_valid(queue_group)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT r.name,i.state,count(i.id) AS count FROM provium_registry r "
                "LEFT JOIN provium_inputs i ON i.pipeline=r.name AND i.queue_group=%s "
                "WHERE r.kind='PipelineConfiguration' "
                "AND (%s::text IS NULL OR r.name=%s) "
                "GROUP BY r.name,i.state ORDER BY r.name,i.state",
                (queue_group, pipeline, pipeline),
            )
            summaries: dict[str, dict[str, Any]] = {}
            for row in rows:
                summary = summaries.setdefault(
                    row["name"],
                    {
                        "pipeline": row["name"],
                        "queue": queue_group,
                        "total": 0,
                        "states": dict.fromkeys(
                            ("pending", "partial", "cancelled", "failed", "completed"),
                            0,
                        ),
                    },
                )
                if row["state"] is not None:
                    summary["states"][row["state"]] = row["count"]
                    summary["total"] += row["count"]
            return list(summaries.values())

    def unregister(self, documents: list[dict[str, Any]]) -> dict[str, int]:
        """Atomically remove registry identities and a pipeline's queue records.

        Missing objects are a no-op. Shared computations and artifacts survive.
        Documents are selected only by kind/name; their old graph need not resolve.
        """
        keys: set[tuple[str, str]] = set()
        for document in documents:
            kind, name = document.get("kind"), document.get("name")
            if kind not in ("PipelineConfiguration", "ComputeProfile"):
                raise ValueError("unknown registry document kind")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("unregister requires a nonempty object name")
            keys.add((kind, name))
        result = {"unregistered": 0, "removed_inputs": 0}
        with self.connect() as connection:
            # Serialize registry changes and final enqueue insertion, not artifact IO.
            connection.execute("SELECT pg_advisory_xact_lock(742819406)")
            for kind, name in sorted(keys):
                if kind == "PipelineConfiguration":
                    result["removed_inputs"] += connection.execute(
                        "DELETE FROM provium_inputs WHERE pipeline=%s", (name,)
                    ).rowcount
                    connection.execute(
                        "DELETE FROM provium_definitions WHERE name=%s", (name,)
                    )
                result["unregistered"] += connection.execute(
                    "DELETE FROM provium_registry WHERE kind=%s AND name=%s",
                    (kind, name),
                ).rowcount
        return result

    def get(self, kind: str, name: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT document FROM provium_registry WHERE kind=%s AND name=%s",
                (kind, name),
            ).fetchone()
            if row is None:
                raise ValueError(f"unregistered {kind}: {name}")
            return row["document"]

    def definition(self, pipeline: str) -> PipelineDefinition | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT document FROM provium_definitions WHERE name=%s",
                (pipeline,),
            ).fetchone()
            return (
                None
                if row is None
                else PipelineDefinition.from_snapshot(row["document"])
            )

    def enqueue(
        self,
        pipeline: str,
        inputs: dict[str, Any],
        *,
        queue_group: str = "default",
        destinations: dict[str, str] | None = None,
    ) -> int:
        with self.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock_shared(742819407)")
            return self._enqueue(
                pipeline, inputs, queue_group=queue_group, destinations=destinations
            )

    def _enqueue(
        self,
        pipeline: str,
        inputs: dict[str, Any],
        *,
        queue_group: str = "default",
        destinations: dict[str, str] | None = None,
    ) -> int:
        assert_queue_name_is_valid(queue_group)
        from provium_pipeline.store import ArtifactStore, local_path

        snapshot = self.get("PipelineConfiguration", pipeline)
        definition = self.definition(pipeline)
        definition_payload = definition.snapshot() if definition else None
        declared_inputs = PipelineConfiguration.model_validate(snapshot).inputs
        expected_inputs = declared_inputs
        if destinations is not None:
            from provium_pipeline.planning import build_plan, select_targets
            from provium_pipeline.requests import ExecutionRequest

            request = ExecutionRequest(inputs, destinations)
            destinations = {
                name: str(local_path(path))
                if urlparse(path).scheme in ("", "file")
                else path
                for name, path in request.output_destinations.items()
            }
            selected = select_targets(
                build_plan(PipelineConfiguration.model_validate(snapshot), definition),
                list(destinations),
            )
            expected_inputs = selected.input_types
        if not expected_inputs.keys() <= inputs.keys() <= declared_inputs.keys():
            raise ValueError("input names differ from pipeline configuration")
        inputs = {name: inputs[name] for name in expected_inputs}
        pinned: dict[str, Any] = {}
        with tempfile.TemporaryDirectory(prefix="provium-enqueue-") as directory:
            reader = ArtifactStore(directory, scratch=directory)
            for name, location in inputs.items():
                artifact = reader.inspect(location)
                pinned[name] = {
                    "location": artifact.location,
                    "identity": artifact.identity,
                    "fingerprint": artifact.fingerprint,
                }
        inputs = pinned
        for name, artifact in inputs.items():
            if artifact["fingerprint"]["type"] != expected_inputs[name]:
                raise ValueError(f"incompatible pipeline input: {name}")
        key = canonical_hash(
            {
                "pipeline": snapshot,
                "definition": definition_payload,
                "inputs": {
                    name: {
                        "identity": item["identity"],
                        "fingerprint": item["fingerprint"],
                    }
                    for name, item in inputs.items()
                },
            }
        )
        if destinations is not None:
            key = canonical_hash({"request": key, "destinations": destinations})
        with self.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(742819406)")
            current = connection.execute(
                "SELECT r.document,d.document AS definition FROM provium_registry r "
                "LEFT JOIN provium_definitions d ON d.name=r.name "
                "WHERE r.kind='PipelineConfiguration' AND r.name=%s",
                (pipeline,),
            ).fetchone()
            if (
                current is None
                or current["document"] != snapshot
                or current["definition"] != definition_payload
            ):
                raise ValueError("pipeline changed or was unregistered during enqueue")
            row = connection.execute(
                "INSERT INTO provium_inputs"
                "(pipeline,snapshot,inputs,dedup,definition,queue_group,destinations) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(queue_group,dedup) DO UPDATE "
                "SET dedup=EXCLUDED.dedup RETURNING id",
                (
                    pipeline,
                    Jsonb(snapshot),
                    Jsonb(inputs),
                    key,
                    Jsonb(definition_payload),
                    queue_group,
                    Jsonb(destinations),
                ),
            ).fetchone()
            return row["id"]

    def pending(
        self,
        pipeline: str,
        after: int = 0,
        limit: int = 100,
        include_finished: bool = False,
        *,
        queue_group: str = "default",
    ) -> list[dict[str, Any]]:
        assert_queue_name_is_valid(queue_group)
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM provium_inputs WHERE pipeline=%s "
                    "AND queue_group=%s AND id>%s "
                    "AND (%s OR state IN ('pending','partial','cancelled')) "
                    "ORDER BY id LIMIT %s",
                    (pipeline, queue_group, after, include_finished, limit),
                )
            )

    def progress(self, identity: int, state: str, detail: dict[str, Any]) -> None:
        with self.connect() as connection:
            # Completion is monotone; a concurrent partial scan cannot erase it.
            connection.execute(
                "UPDATE provium_inputs SET state=%s,detail=%s WHERE id=%s "
                "AND (state NOT IN ('completed','failed') OR %s='completed')",
                (state, Jsonb(detail), identity, state),
            )

    def retry(self, pipeline: str, *, queue_group: str = "default") -> int:
        assert_queue_name_is_valid(queue_group)
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE provium_inputs SET state='pending' "
                "WHERE pipeline=%s AND queue_group=%s AND state='failed'",
                (pipeline, queue_group),
            )
            return cursor.rowcount

    def result(self, namespace: str, key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT result FROM provium_computations "
                "WHERE namespace=%s AND key=%s AND "
                "(state='complete' OR (state='running' AND expires<=clock_timestamp() "
                "AND result IS NOT NULL))",
                (namespace, key),
            ).fetchone()
            return None if row is None else row["result"]

    def acquire(
        self,
        namespace: str,
        key: str,
        run: str,
        *,
        force: bool = False,
        ttl: float = 60,
        input_id: int | None = None,
    ) -> Lease | None:
        owner = uuid4().hex
        with self.connect() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock_shared(hashtext(%s), 83451)",
                (namespace,),
            )
            # Admission and deletion serialize on the InputSet row. A lease already
            # admitted may finish; deleting a queue never revokes shared computation.
            if input_id is not None:
                row = connection.execute(
                    "SELECT id FROM provium_inputs WHERE id=%s FOR KEY SHARE",
                    (input_id,),
                ).fetchone()
                if row is None:
                    return None
            connection.execute(
                "INSERT INTO provium_computations(namespace,key) VALUES(%s,%s) "
                "ON CONFLICT DO NOTHING",
                (namespace, key),
            )
            row = connection.execute(
                "UPDATE provium_computations SET owner=%s,run=%s,"
                "generation=generation+1,"
                "expires=clock_timestamp()+%s*interval '1 second',"
                "state='running' WHERE namespace=%s AND key=%s "
                "AND (owner IS NULL OR expires<=clock_timestamp()) "
                "AND (result IS NULL OR %s) "
                "AND NOT (state='running' AND run=%s) "
                "AND NOT EXISTS(SELECT 1 FROM provium_failures "
                "WHERE namespace=%s AND key=%s AND run=%s) RETURNING generation",
                (owner, run, ttl, namespace, key, force, run, namespace, key, run),
            ).fetchone()
            return (
                None
                if row is None
                else Lease(namespace, key, owner, row["generation"], run)
            )

    def renew(self, lease: Lease, *, ttl: float = 60) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE provium_computations SET "
                "expires=clock_timestamp()+%s*interval '1 second' "
                "WHERE namespace=%s AND key=%s AND owner=%s AND generation=%s "
                "AND expires>clock_timestamp()",
                (ttl, lease.namespace, lease.key, lease.owner, lease.generation),
            )
            return cursor.rowcount == 1

    def complete(self, lease: Lease, outputs: dict[str, Any]) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE provium_computations SET result=%s,state='complete',"
                "owner=NULL,expires=NULL "
                "WHERE namespace=%s AND key=%s AND owner=%s AND generation=%s "
                "AND expires>clock_timestamp()",
                (
                    Jsonb(outputs),
                    lease.namespace,
                    lease.key,
                    lease.owner,
                    lease.generation,
                ),
            )
            return cursor.rowcount == 1

    def fail(self, lease: Lease, error: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO provium_failures VALUES(%s,%s,%s,%s) ON "
                "CONFLICT DO NOTHING",
                (lease.namespace, lease.key, lease.run, error),
            )
            connection.execute(
                "UPDATE provium_computations SET state=CASE WHEN result IS NULL "
                "THEN 'failed' ELSE 'complete' END,owner=NULL,expires=NULL "
                "WHERE namespace=%s AND key=%s AND owner=%s AND generation=%s",
                (lease.namespace, lease.key, lease.owner, lease.generation),
            )
