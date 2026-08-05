"""pgvector `VectorStore`: the second adapter, and the test of the port.

Every SQL string in this library's vector storage lives here. The port speaks
domain types, so nothing above this module knows Postgres is involved.

## Four decisions worth knowing before reading the queries

**asyncpg directly, no ORM.** Slice 5's brief permitted SQLAlchemy inside this
adapter, and it earned nothing here: there are six statements, three of them
shaped by pgvector operators an ORM would only get in the way of, and two of
them (`EXPLAIN`, and the `unnest` batch insert) are exactly the kind of SQL an
ORM makes harder to write and read.

That decision is why **redstring has no first-party SQLAlchemy import and no
declared SQLAlchemy dependency.** Slice 9 deleted the relational layer, and
this module was the only thing that could have kept SQLAlchemy in
`pyproject.toml`; because it never reached for it, the removal cost nothing.
asyncpg is the only database driver this library drives itself.

Stated that precisely because the stronger claim is false and worth knowing:
**SQLAlchemy is still installed.** `eventsource-py` requires it in its *base*
dependencies -- not behind an extra -- so any environment with the
`eventsourcing` extra has it importable, and that is a runtime path rather
than a dev one. cosmic-ray pulls it too. The distinction that matters is
therefore "no code here imports it and nothing here asks for it", not "it is
absent"; a reader who assumes the latter would be surprised by `uv.lock`.

**No ANN index, deliberately -- and this is the most important line in the
file.** The obvious build is an `hnsw` or `ivfflat` index on `embedding`. It is
wrong for a multi-tenant store, and wrong in a way that produces plausible
results rather than an error:

- If the planner uses the vector index, it finds the `k` globally nearest rows
  and *then* drops the ones belonging to other tenants. A tenant holding 1% of
  the table gets a handful of results, or none, for a query with thousands of
  genuine neighbours. The port forbids exactly this ("filters are applied
  before `k`"), and no test that only inspects results can tell it from a
  tenant with little data.
- If the planner filters by tenant first, it does not use the vector index at
  all, and the index costs write throughput to be never read.

Both are "correct". Both are wrong. So this adapter scans **within the
tenant**, served by the primary key's leading `tenant_id` column, and is
therefore *exact* -- it does not merely pass the compliance suite's recall
tier, it passes the exact tier for the same reason the in-memory adapter does.
The cost is linear in one tenant's rows rather than logarithmic. See BACKLOG
B10k for the three ways out (partition per tenant, pgvector 0.8 iterative
scan, or a per-tenant partial index) and what each would cost.

**One table per dimension, checked on connect.** `vector(n)` bakes the
dimension into the column type, so a store built for 768 cannot share a table
with one built for 1024. `ensure_schema` reads the declared typmod back and
raises `DimensionMismatchError` rather than letting the first insert fail with
a Postgres error that names neither store nor model.

**`entity_type` is a real column, derived from `metadata`.** The port filters
on `metadata["entity_type"]`; doing that as `metadata->>'entity_type'` on every
row is a per-row JSON parse the planner cannot index. The column is written
from the metadata and the metadata is stored whole, so the JSON stays the
source of truth and the column is a projection of it.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Self

from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.vector import VectorMatch, VectorRecord, has_zero_norm
from redstring.ports.vector_store import entity_type_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    import asyncpg

    from redstring.domain.ids import EntityId, TenantId

#: Table names are interpolated into SQL -- Postgres has no parameter form for
#: an identifier -- so the name is proved to be a bare lowercase identifier
#: first. Anything else, including a quoted or schema-qualified name, is
#: rejected rather than escaped: the set of names this store needs is small,
#: and a rejected name is a clearer failure than a subtly mis-escaped one.
#:
#: This guard is what the five `# nosec B608` markers below rest on: bandit
#: sees an f-string in a SQL literal and cannot see that the only interpolated
#: value was proved safe in `__init__`, nor that every *caller-supplied* value
#: travels as a `$n` parameter. Deleting the guard without deleting those
#: markers would leave real injection unreported, so
#: `test_a_table_name_that_is_not_a_bare_identifier_is_rejected` -- which
#: includes a `"; DROP TABLE users; --` case -- is the thing keeping them
#: honest, not the comment.
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

#: The score expression, in one place. `<=>` is pgvector's **cosine distance**
#: (`1 - cosine`), so this is `(1 + cosine) / 2` -- the scale the port defines.
#: Getting this backwards is the silent-inversion bug the port warns about, and
#: `tests/compliance/vector_store.py` pins the resulting numbers against
#: `redstring.domain.vector.cosine_score` rather than merely their order.
_SCORE = "1 - (embedding <=> $2::vector) / 2"


class PgVectorStore:
    """A `VectorStore` backed by Postgres with the `vector` extension."""

    def __init__(
        self, pool: asyncpg.Pool[Any], *, dimension: int, table: str = "kg_vectors"
    ) -> None:
        """Wrap an existing pool. `close()` will not close it.

        Ownership follows who created the pool, as it does on the Neo4j
        adapter: a caller that injected one keeps the right to close it, and
        `connect()` builds its own and does close it. Without that split,
        disposing a store per hypothesis example would take the shared pool
        down with the first one.
        """
        if dimension <= 0:
            raise ValueError(f"dimension must be positive, not {dimension}")
        if not _IDENTIFIER.fullmatch(table):
            raise ValueError(f"table must be a bare lowercase identifier, not {table!r}")
        self._pool = pool
        self._dimension = dimension
        self._table = table
        self._owns_pool = False

    @classmethod
    async def connect(
        cls,
        dsn: str,
        *,
        dimension: int,
        table: str = "kg_vectors",
        # Passed straight to `asyncpg.create_pool`, whose own signature is
        # `**kwargs`; narrowing it here would mean restating asyncpg's options
        # and going stale against them.
        **pool_options: Any,  # noqa: ANN401
    ) -> Self:
        """Build a store owning a pool of its own, which `close()` closes."""
        try:
            import asyncpg
        except ImportError as error:  # pragma: no cover - needs asyncpg absent
            raise ImportError(
                "PgVectorStore.connect needs asyncpg: install `redstring[pgvector]`"
            ) from error

        pool = await asyncpg.create_pool(dsn, **pool_options)
        store = cls(pool, dimension=dimension, table=table)
        store._owns_pool = True
        return store

    async def close(self) -> None:
        """Release the pool, if this store created it."""
        if self._owns_pool:
            await self._pool.close()

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def table(self) -> str:
        return self._table

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def ensure_schema(self) -> None:
        """Create the extension, table and indexes. Idempotent.

        Raises `DimensionMismatchError` if the table already exists with a
        different declared dimension -- see the module docstring on why that
        check is here rather than left to the first failing insert.
        """
        async with self._pool.acquire() as connection:
            await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            for statement in self._schema_statements():
                await connection.execute(statement)
            declared = await connection.fetchval(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = $1::regclass AND attname = 'embedding' AND NOT attisdropped",
                self._table,
            )
        if declared is not None and declared != self._dimension:
            raise DimensionMismatchError(expected=int(declared), actual=self._dimension)

    def _schema_statements(self) -> tuple[str, ...]:
        """The DDL, as data, so a server-free test can read it.

        The primary key leads with `tenant_id` because every query filters on
        it and there is no cross-tenant read: a key on `entity_id` alone would
        also reject the same id under two tenants, which is the arrangement
        the isolation properties depend on most.

        Both btrees lead with `tenant_id`, which is what turns a tenant-scoped
        read into a seek rather than a scan of every tenant's rows -- the trap
        slice 4 hit on Neo4j, where correct results hid a whole-database scan
        that no behavioural test could see. There is deliberately no third
        index on `tenant_id` alone (either of these serves it) and deliberately
        no index on `embedding` (see the module docstring).
        """
        return (
            f"CREATE TABLE IF NOT EXISTS {self._table} ("
            "  tenant_id uuid NOT NULL,"
            "  entity_id uuid NOT NULL,"
            f"  embedding vector({self._dimension}) NOT NULL,"
            "  entity_type text,"
            "  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,"
            "  PRIMARY KEY (tenant_id, entity_id)"
            ")",
            f"CREATE INDEX IF NOT EXISTS {self._table}_tenant_type_idx "
            f"ON {self._table} (tenant_id, entity_type)",
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def upsert(
        self,
        entity_id: EntityId,
        vector: Sequence[float],
        tenant_id: TenantId,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self.upsert_many(
            [
                VectorRecord(
                    entity_id=entity_id,
                    tenant_id=tenant_id,
                    vector=list(vector),
                    metadata=metadata or {},
                )
            ]
        )

    async def upsert_many(self, items: Sequence[VectorRecord]) -> None:
        # Validate `items`, **not** the deduplicated rows. Collapsing first
        # would let a rejected record vanish because a later one happened to
        # replace its key, so the same call would raise here and succeed
        # in-memory -- the divergence the shared compliance suite exists to
        # catch, and it did not, because its earlier test used two distinct
        # keys. `test_upsert_many_validates_every_element_including_superseded_ones`
        # now pins the order.
        for record in items:
            self._check(record.vector)

        rows = deduplicate(items)
        if not rows:
            return

        await self._pool.execute(
            self._insert_sql(),
            [record.tenant_id for record in rows],
            [record.entity_id for record in rows],
            [encode_vector(record.vector) for record in rows],
            [entity_type_of(record.metadata) for record in rows],
            [json.dumps(record.metadata) for record in rows],
        )

    def _insert_sql(self) -> str:
        """One statement for the whole batch, not a loop.

        Embedding batches are thousands of rows, so `unnest` over five arrays
        keeps this a single round trip. `ON CONFLICT DO UPDATE` is what makes
        the write idempotent and last-write-wins; it is also why `deduplicate`
        has to run first, because Postgres refuses to let one statement affect
        the same row twice.
        """
        return (
            f"INSERT INTO {self._table} (tenant_id, entity_id, embedding, entity_type, metadata) "  # nosec B608
            "SELECT * FROM unnest("
            "  $1::uuid[], $2::uuid[], $3::vector[], $4::text[], $5::jsonb[]"
            ") "
            "ON CONFLICT (tenant_id, entity_id) DO UPDATE SET "
            "  embedding = EXCLUDED.embedding, "
            "  entity_type = EXCLUDED.entity_type, "
            # Replaced wholesale rather than merged with `||`: a merge would
            # let a key removed by a later event survive, which makes replay
            # order-dependent.
            "  metadata = EXCLUDED.metadata"
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, entity_id: EntityId, tenant_id: TenantId) -> VectorRecord | None:
        row = await self._pool.fetchrow(
            # `::real[]` rather than reading the `vector` back directly.
            # pgvector's *text* output rounds to seven significant digits --
            # 128.390625 comes back as 128.39062 -- so a text round trip is
            # lossy even though the stored float4 is exact. Casting to
            # `real[]` hands asyncpg a binary float4 array, which decodes
            # exactly. Found by the round-trip property, not by reading docs.
            f"SELECT embedding::real[] AS vector, metadata FROM {self._table} "  # nosec B608
            "WHERE tenant_id = $1 AND entity_id = $2",
            tenant_id,
            entity_id,
        )
        if row is None:
            return None
        return VectorRecord(
            entity_id=entity_id,
            tenant_id=tenant_id,
            vector=list(row["vector"]),
            metadata=json.loads(row["metadata"]),
        )

    async def search(
        self,
        vector: Sequence[float],
        tenant_id: TenantId,
        *,
        k: int = 10,
        entity_types: Sequence[str] | None = None,
        min_score: float | None = None,
    ) -> list[VectorMatch]:
        self._check(vector)
        if k < 0:
            raise ValueError("k must not be negative")
        if k == 0:
            # `LIMIT 0` would answer correctly; not asking is cheaper, and the
            # port promises `[]` regardless of what the tenant holds.
            return []

        rows = await self._pool.fetch(
            self._search_sql(),
            tenant_id,
            encode_vector(vector),
            entity_types is None,
            list(entity_types or ()),
            min_score,
            k,
        )
        return [
            VectorMatch(
                entity_id=row["entity_id"],
                # Clamped, because float32 arithmetic can put an identical
                # pair marginally above 1.0 and `VectorMatch` bounds the field.
                score=min(1.0, max(0.0, float(row["score"]))),
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    def _search_sql(self) -> str:
        """Filter, then rank, then limit -- in that order, in one statement.

        `WHERE` runs before `ORDER BY` and `LIMIT`, so SQL gives the port's
        "filters are applied before `k`" rule for free. That is only true
        while there is no ANN index; with one, the planner may reach for the
        index and post-filter, which is the trap the module docstring
        describes.

        The tie-break is `entity_id::text` ascending, the port's documented
        total order, so `k` cutting through a tie cuts the same way here as
        in-memory.
        """
        return (
            f"SELECT entity_id, metadata, {_SCORE} AS score "  # nosec B608
            f"FROM {self._table} "
            "WHERE tenant_id = $1 "
            # `$3` is "no type filter". An empty `$4` array matches nothing,
            # and a NULL `entity_type` matches no filter -- both are what the
            # port specifies.
            "  AND ($3 OR entity_type = ANY($4::text[])) "
            f"  AND ($5::float8 IS NULL OR {_SCORE} >= $5) "
            "ORDER BY score DESC, entity_id::text ASC "
            "LIMIT $6"
        )

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    async def delete(self, entity_id: EntityId, tenant_id: TenantId) -> bool:
        deleted = await self._pool.fetchval(
            f"DELETE FROM {self._table} WHERE tenant_id = $1 AND entity_id = $2 RETURNING 1",  # nosec B608
            tenant_id,
            entity_id,
        )
        return deleted is not None

    async def delete_by_tenant(self, tenant_id: TenantId) -> int:
        # Counted through a CTE rather than by parsing asyncpg's "DELETE n"
        # status string, which is a stringly-typed answer to a numeric
        # question and one release note away from changing shape.
        removed = await self._pool.fetchval(
            f"WITH removed AS (DELETE FROM {self._table} "  # nosec B608
            "WHERE tenant_id = $1 RETURNING 1) SELECT count(*) FROM removed",
            tenant_id,
        )
        return int(removed)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _check(self, vector: Sequence[float]) -> None:
        """Reject anything the port says is not a vector for this store.

        Client-side on purpose. Postgres would reject a wrong length too, but
        as an opaque `expected 8 dimensions, not 3` with no mention of which
        store or model, and only after a round trip -- and it would not reject
        a zero vector at all: `<=>` against one yields NaN, which sorts
        unpredictably and would make ranking depend on the query plan.
        """
        if len(vector) != self._dimension:
            raise DimensionMismatchError(expected=self._dimension, actual=len(vector))
        if has_zero_norm(vector):
            raise ValueError("a zero vector has no direction; cosine is undefined for it")


# ----------------------------------------------------------------------
# Encoding
#
# Module-level and pure, so the default commit gate can execute them without a
# server. Slice 4 learned that an integration-only adapter is invisible to the
# gate: a cosmic-ray mutant in its source passed the entire suite because not
# one line of it ran.
# ----------------------------------------------------------------------


def encode_vector(vector: Sequence[float]) -> str:
    """Render a vector as pgvector's text input form, `[1,2,3]`.

    Text rather than `pgvector.asyncpg.register_vector`: the input wire
    format is stable and documented, and a pure function is testable without a
    connection, which is the whole point of this section. `repr` is used per
    component because a shortened rendering would silently reduce precision
    before Postgres ever saw the value.

    There is deliberately **no** matching `decode_vector`. pgvector's text
    *output* rounds to seven significant digits, so parsing it back is lossy;
    reads cast to `real[]` and let asyncpg decode float4 binary instead. The
    asymmetry is real and the round-trip property is what found it.
    """
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def deduplicate(items: Sequence[VectorRecord]) -> list[VectorRecord]:
    """Collapse repeated `(tenant_id, entity_id)` keys, keeping the last.

    Required, not an optimisation: `ON CONFLICT DO UPDATE` raises "cannot
    affect row a second time" when one statement touches a row twice, and
    `upsert_many([record, record])` is an ordinary call. The key is the
    **ordered pair** -- `(x, y)` and `(y, x)` are different rows, which a key
    built from an unordered pair or an XOR of the two would silently merge.
    """
    return list({(record.tenant_id, record.entity_id): record for record in items}.values())
