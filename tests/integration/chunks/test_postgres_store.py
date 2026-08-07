"""The Postgres `ChunkStore` adapter against the shared compliance suite.

Everything asserted about behaviour lives in `tests.compliance.chunk_store`.
This module supplies a store and adds only what is specific to *this*
adapter -- schema, the column type the ordering depends on, and round-trip
cost.

Start the backend deliberately::

    docker compose -f docker-compose.test.yml up -d postgres
    uv run pytest -m integration tests/integration/chunks/ -v

`-m integration` is required: `addopts` excludes the marker so the commit gate
stays infra-free. And this suite needs its **own invocation**, separate from
`tests/unit/chunks/` -- two subclasses of one compliance class in one process
is BACKLOG B10m.

## Why each xdist worker gets its own table

Copied from `tests/integration/vector/test_pgvector_store.py`, and for the
reason recorded there: the Neo4j suite resets one shared database and loses 36
tests to it under `-n auto` (B10f). The table name carries
`PYTEST_XDIST_WORKER`, so a worker truncates only its own rows.

## Why the skip probe writes a row

A TCP connect proves the port answers, not that the server can serve; a
Postgres still starting accepts connections. The probe creates a temporary
table and round-trips a row through it. This repo has paid for the weaker
check once already (BACKLOG B12).

Unlike pgvector's probe there is no extension to create: this adapter needs
plain Postgres, which is the only thing it asks of a deployment.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest

from redstring.chunks.adapters.postgres import PostgresChunkStore
from redstring.domain.chunk import chunk_id
from tests.compliance.chunk_store import ChunkStoreCompliance

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg

    from redstring.ports.chunk_store import ChunkStore

pytestmark = pytest.mark.integration

DSN = os.environ.get(
    "KG_TEST_POSTGRES_DSN", "postgresql://postgres:redstring@localhost:5434/redstring_test"
)

#: One table per xdist worker; see the module docstring. `gw0` and friends are
#: already valid bare identifiers, and the adapter rejects anything that is not.
TABLE = f"kg_chunks_test_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"


async def _probe() -> asyncpg.Pool[Any] | None:
    """A connected pool, or `None` if Postgres cannot serve a trivial write."""
    try:
        import asyncpg
    except ImportError:  # pragma: no cover - asyncpg is installed with the extra
        return None

    try:
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
    except Exception:
        return None
    if pool is None:  # pragma: no cover - defensive; create_pool raises instead
        return None
    try:
        async with pool.acquire() as connection:
            await connection.execute(
                "CREATE TEMP TABLE _kg_chunk_probe (id text) ON COMMIT PRESERVE ROWS"
            )
            await connection.execute("INSERT INTO _kg_chunk_probe VALUES ('ok')")
            if await connection.fetchval("SELECT id FROM _kg_chunk_probe") != "ok":
                raise RuntimeError("the probe row did not come back")
    except Exception:
        await pool.close()
        return None
    return pool


_schema_ready = False


@pytest.fixture
async def pool() -> AsyncIterator[asyncpg.Pool[Any]]:
    """A pool for one test, or skip it.

    Function-scoped because an asyncpg pool binds to the event loop that
    created it, and `asyncio_default_fixture_loop_scope` is `function`.
    """
    global _schema_ready

    connected = await _probe()
    if connected is None:
        pytest.skip(
            f"Postgres is not serving at {DSN}. Start it with "
            f"`docker compose -f docker-compose.test.yml up -d postgres`."
        )
    if not _schema_ready:
        await PostgresChunkStore(connected, table=TABLE).ensure_schema()
        _schema_ready = True
    try:
        yield connected
    finally:
        await connected.close()


async def _columns(pool: asyncpg.Pool[Any], table: str) -> list[tuple[str, str, str | None]]:
    """One table's column list, as `(name, type, default)` in ordinal order.

    The default is included because `entity_ids uuid[] NOT NULL DEFAULT '{}'`
    and the same column without a default are two different schemas that carry
    the same name and type.
    """
    rows = await pool.fetch(
        "SELECT column_name, data_type, column_default FROM information_schema.columns "
        "WHERE table_name = $1 ORDER BY ordinal_position",
        table,
    )
    return [(row["column_name"], row["data_type"], row["column_default"]) for row in rows]


async def _truncate(pool: asyncpg.Pool[Any]) -> None:
    """Empty this worker's table.

    The reset lives here, not on the adapter: "delete every tenant's rows" is
    a test affordance, and a production `ChunkStore` should not offer one.
    `delete_by_tenant` is the port's bulk removal.
    """
    await pool.execute(f"TRUNCATE {TABLE}")


class TestPostgresChunkStore(ChunkStoreCompliance):
    """The whole compliance suite, unchanged, against real Postgres."""

    @pytest.fixture(autouse=True)
    def _pool(self, pool: asyncpg.Pool[Any]) -> None:
        # Stashed on the instance because `new_store` takes no arguments: the
        # suite's contract is that an adapter supplies exactly one thing.
        self._shared_pool = pool

    async def new_store(self) -> ChunkStore:
        await _truncate(self._shared_pool)
        return PostgresChunkStore(self._shared_pool, table=TABLE)

    async def dispose(self, store: ChunkStore) -> None:
        assert isinstance(store, PostgresChunkStore)
        await store.close()


class TestPostgresChunkStoreSpecifics:
    """Behaviour the port does not specify, so the compliance suite cannot."""

    @pytest.fixture
    async def store(self, pool: asyncpg.Pool[Any]) -> AsyncIterator[PostgresChunkStore]:
        await _truncate(pool)
        built = PostgresChunkStore(pool, table=TABLE)
        try:
            yield built
        finally:
            await built.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def test_ensure_schema_is_idempotent(self, store: PostgresChunkStore) -> None:
        await store.ensure_schema()
        await store.ensure_schema()

    async def test_ensure_schema_creates_the_table_from_nothing(
        self, pool: asyncpg.Pool[Any]
    ) -> None:
        """The DDL must actually run.

        Every other test here works against a table an earlier run created, so
        **the schema statements could do nothing at all and nothing would
        notice** -- cosmic-ray proved that against the pgvector adapter by
        replacing the loop's iterable with `[]`. The only test that can see it
        is one starting from no table.
        """
        table = f"{TABLE}_fresh"
        await pool.execute(f"DROP TABLE IF EXISTS {table}")
        assert not await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", table)

        fresh = PostgresChunkStore(pool, table=table)
        await fresh.ensure_schema()

        assert await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", table)
        # Usable, not merely present.
        tenant = uuid4()
        written = ChunkStoreCompliance._chunk(tenant, "doc-1", "a passage", chunk_index=0)
        await fresh.upsert_many([written])
        assert await fresh.get(written.id, tenant) == written

        # And the table the other 53 tests use matches what the adapter
        # *currently* declares. This half is about the harness, not the DDL,
        # and without it the module has a standing false-green: `_schema_ready`
        # is a module-level global and the DDL is `CREATE TABLE IF NOT
        # EXISTS`, so a change to the column list or a column type has no
        # effect on any run until someone drops the worker table by hand. A
        # sabotage touching only the DDL would leave the whole suite green and
        # the schema change invisible.
        #
        # Compared rather than silently repaired with a `DROP TABLE`: dropping
        # would make a stale table a thing that quietly heals, and the failure
        # a developer needs to see is "your table predates this schema" --
        # which is also the machine-versus-CI disagreement this catches.
        assert await _columns(pool, table) == await _columns(pool, TABLE), (
            f"{TABLE} does not match the schema {type(fresh).__name__} now "
            f"declares. `CREATE TABLE IF NOT EXISTS` cannot migrate it, so "
            f"every other test in this module is running against a stale "
            f"table. Drop it: DROP TABLE {TABLE};"
        )

        await pool.execute(f"DROP TABLE {table}")

    async def test_chunk_index_is_an_integer_column(
        self, store: PostgresChunkStore, pool: asyncpg.Pool[Any]
    ) -> None:
        """The schema mistake the compliance suite can only see through data.

        A `text` column stores, reads and round-trips every chunk correctly and
        returns chunk 10 before chunk 2. The suite catches it with an index-10
        case; this asserts the cause directly, so a failure names the column
        rather than an out-of-order list.
        """
        await store.ensure_schema()
        declared = await pool.fetchval(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = 'chunk_index'",
            TABLE,
        )
        assert declared == "integer"

    async def test_the_primary_key_is_the_pair_not_the_id(
        self, store: PostgresChunkStore, pool: asyncpg.Pool[Any]
    ) -> None:
        """Content addressing makes an `id`-only key a live defect.

        The same passage of the same source under two tenants hashes
        identically, so a key on `id` alone would make one tenant's write
        replace another's row.
        """
        await store.ensure_schema()
        columns = await pool.fetch(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = $1::regclass AND i.indisprimary "
            "ORDER BY array_position(i.indkey, a.attnum)",
            TABLE,
        )
        assert [row["attname"] for row in columns] == ["tenant_id", "id"]

    async def test_get_by_source_seeks_rather_than_scanning_the_table(
        self, store: PostgresChunkStore, pool: asyncpg.Pool[Any]
    ) -> None:
        """The results are identical either way, so only the plan can see this.

        Slice 4's tenant-scoped Neo4j reads planned as a full scan across every
        tenant while returning the right answers. The dataset is large enough
        (12k rows over 300 tenants) that the planner has a real choice.

        **Deliberately not asserted: the absence of a `Sort` node.** The index
        covers the full ordering and *can* be walked in order, but with forty
        matching rows the planner prefers a bitmap scan and an in-memory sort,
        which is the cheaper plan and an equally correct one. Pinning the
        ordered index scan would make this test a statement about the row
        count rather than about the schema -- the same over-specification the
        pgvector plan test records having already been bitten by. What the
        index must do is turn the *filter* into a seek; the ordering is a
        contract the compliance suite checks against results.
        """
        tenants = [uuid4() for _ in range(300)]
        await store.upsert_many(
            [
                ChunkStoreCompliance._chunk(
                    tenant, f"doc-{index % 4}", f"passage {tenant} {index}", chunk_index=index
                )
                for tenant in tenants
                for index in range(40)
            ]
        )
        await pool.execute(f"ANALYZE {TABLE}")

        rows = await pool.fetch(
            "EXPLAIN (ANALYZE false, COSTS false, VERBOSE true) "
            f"SELECT * FROM {TABLE} WHERE tenant_id = $1 AND source_id = $2 "
            "ORDER BY chunk_index ASC, id ASC",
            tenants[0],
            "doc-1",
        )
        plan = "\n".join(row["QUERY PLAN"] for row in rows)

        assert "Seq Scan" not in plan, (
            f"a tenant-scoped read of one source reads every row of every tenant:\n{plan}"
        )
        conditions = [line for line in plan.splitlines() if "Index Cond" in line]
        assert conditions, f"no index condition at all:\n{plan}"
        assert "tenant_id" in conditions[0]
        assert "source_id" in conditions[0]

    async def test_the_order_by_alone_produces_the_tie_break(self, pool: asyncpg.Pool[Any]) -> None:
        """The total order is guaranteed **twice**, so neither guarantee is
        falsifiable by any other test in this repository.

        `get_by_source` says `ORDER BY chunk_index ASC, id ASC` *and* the
        covering index `(tenant_id, source_id, chunk_index, id)` supplies that
        order to the planner. Deleting `, id ASC` leaves all 54 tests green,
        including the compliance tie-break case -- and truncating the index to
        `(tenant_id, source_id, chunk_index)` with the clause shortened makes
        that case fail, which is what proves the survivor is not equivalent.
        Whichever mechanism a future author removes, the suite stays green on
        the other.

        So this one takes the index away. With `enable_indexscan` and
        `enable_bitmapscan` off the planner must sequentially scan, and the
        only thing that can produce the order is the clause. The plan is
        asserted too: without that, a version of this test where the settings
        silently failed to apply would be the original unfalsifiable test
        again, wearing a longer docstring.

        The input is the compliance suite's tie-break shape -- two chunks
        sharing `chunk_index=3`, and a third at index 0 holding the id that
        sorts *last*, so `(chunk_index, id)` and `id` alone disagree.
        """
        # A single connection, because `SET` is per-session and a pool hands
        # out whichever connection is free. The adapter only reaches for
        # `fetch`/`fetchrow`/`fetchval`/`execute` on the paths used here, all
        # of which a Connection has with the same signatures.
        async with pool.acquire() as connection:
            await connection.execute(f"TRUNCATE {TABLE}")
            await connection.execute("SET enable_indexscan = off")
            await connection.execute("SET enable_bitmapscan = off")
            store = PostgresChunkStore(cast("asyncpg.Pool[Any]", connection), table=TABLE)

            tenant = uuid4()
            low_tie = ChunkStoreCompliance._chunk(tenant, "doc-1", "passage gamma", chunk_index=3)
            high_tie = ChunkStoreCompliance._chunk(tenant, "doc-1", "passage alpha", chunk_index=3)
            leader = ChunkStoreCompliance._chunk(tenant, "doc-1", "passage beta", chunk_index=0)
            assert low_tie.id < high_tie.id < leader.id
            await store.upsert_many([high_tie, low_tie, leader])

            plan = "\n".join(
                row["QUERY PLAN"]
                for row in await connection.fetch(
                    "EXPLAIN (ANALYZE false, COSTS false) "
                    f"SELECT * FROM {TABLE} WHERE tenant_id = $1 AND source_id = $2 "
                    "ORDER BY chunk_index ASC, id ASC",
                    tenant,
                    "doc-1",
                )
            )
            assert "Seq Scan" in plan, (
                f"the index is still serving this query, so the ORDER BY is "
                f"still unobservable:\n{plan}"
            )
            assert "Sort" in plan, f"nothing is sorting, so nothing is ordering:\n{plan}"

            found = await store.get_by_source("doc-1", tenant)

        assert [chunk.id for chunk in found] == [leader.id, low_tie.id, high_tie.id]

    # ------------------------------------------------------------------
    # Round-trip cost
    #
    # `upsert_many` and `replace_source` are each one statement by contract --
    # the port says a chunking is thousands of rows and that the fold must be
    # atomic. That is a property of this adapter, so the compliance suite
    # cannot assert it, and it is the exact regression a refactor introduces
    # silently.
    # ------------------------------------------------------------------

    async def test_upsert_many_is_one_statement(
        self, store: PostgresChunkStore, pool: asyncpg.Pool[Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tenant = uuid4()
        chunks = [
            ChunkStoreCompliance._chunk(tenant, "doc-1", f"passage {i}", chunk_index=i)
            for i in range(250)
        ]
        executed: list[str] = []
        original = type(pool).execute

        async def counting(self: Any, query: str, *args: Any, **kwargs: Any) -> Any:
            executed.append(query)
            return await original(self, query, *args, **kwargs)

        monkeypatch.setattr(type(pool), "execute", counting)

        await store.upsert_many(chunks)

        assert len(executed) == 1, f"{len(executed)} statements for 250 rows: this is a loop"
        assert len(await store.get_by_source("doc-1", tenant)) == 250

    async def test_replace_source_is_one_statement(
        self, store: PostgresChunkStore, pool: asyncpg.Pool[Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two round trips would leave a crash between them mid-fold.

        `fetchval` *and* `execute` are counted: an implementation that deleted
        through one and wrote through the other would pass a check that only
        watched one of them.
        """
        tenant = uuid4()
        old = [
            ChunkStoreCompliance._chunk(tenant, "doc-1", f"old {i}", chunk_index=i)
            for i in range(50)
        ]
        await store.upsert_many(old)
        fresh = [
            ChunkStoreCompliance._chunk(tenant, "doc-1", f"new {i}", chunk_index=i)
            for i in range(50)
        ]

        statements: list[str] = []
        for name in ("execute", "fetchval", "fetch", "fetchrow"):
            original = getattr(type(pool), name)

            def counting(self: Any, query: str, *args: Any, _inner: Any = original) -> Any:
                statements.append(query)
                return _inner(self, query, *args)

            monkeypatch.setattr(type(pool), name, counting)

        removed = await store.replace_source("doc-1", tenant, fresh)

        assert removed == 50
        assert len(statements) == 1, f"replace_source ran {len(statements)} statements"

    # ------------------------------------------------------------------
    # Encoding fidelity
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "metadata",
        [
            pytest.param({}, id="empty"),
            pytest.param({"nested": {"deep": {"deeper": [1, 2, {"x": None}]}}}, id="nested"),
            pytest.param({"empty_dict": {}, "empty_list": []}, id="empty-containers"),
            pytest.param({"mixed": [1, "two", None, True, {"k": []}]}, id="heterogeneous-array"),
            # jsonb keeps `true` and `1` apart; a coercion through int would not.
            pytest.param({"bool": True, "int": 1}, id="bool-is-not-int"),
            pytest.param({"big": 2**70}, id="bignum"),
            pytest.param({"": "empty key", "unicode": "é中\U0001f600"}, id="odd-keys"),
        ],
    )
    async def test_metadata_round_trips_exactly(
        self, store: PostgresChunkStore, metadata: dict[str, Any]
    ) -> None:
        tenant = uuid4()
        written = ChunkStoreCompliance._chunk(
            tenant, "doc-1", "a passage", chunk_index=0, metadata=metadata
        )
        await store.upsert_many([written])

        found = await store.get(written.id, tenant)
        assert found is not None
        assert found.metadata == metadata

    async def test_many_entity_ids_of_differing_lengths_survive_one_batch(
        self, store: PostgresChunkStore
    ) -> None:
        """The binding decision, asserted rather than argued.

        `entity_ids` is a `uuid[]` per row, so parallel-array binding would
        need a nested array -- and Postgres arrays are rectangular, so asyncpg
        rejects unequal-length sub-arrays outright. Chunks carry different
        numbers of entities by construction, which is why the payload is one
        `jsonb` document. A batch whose rows have 0, 1 and 3 entities is the
        input that would have failed.
        """
        tenant = uuid4()
        counts = [0, 1, 3, 2]
        chunks = [
            ChunkStoreCompliance._chunk(
                tenant,
                "doc-1",
                f"passage {i}",
                chunk_index=i,
                entity_ids=[uuid4() for _ in range(count)],
            )
            for i, count in enumerate(counts)
        ]

        await store.upsert_many(chunks)

        found = await store.get_by_source("doc-1", tenant)
        assert [len(chunk.entity_ids) for chunk in found] == counts
        assert found == chunks

    # ------------------------------------------------------------------
    # Table names and connection ownership
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "table",
        ['kg_chunks"; DROP TABLE users; --', "public.kg_chunks", "KgChunks", "", "1_chunks"],
    )
    async def test_a_table_name_that_is_not_a_bare_identifier_is_rejected(
        self, pool: asyncpg.Pool[Any], table: str
    ) -> None:
        """What the `# nosec B608` markers rest on.

        The table name is interpolated because Postgres has no parameter form
        for an identifier, so the guard -- not the marker -- is what makes the
        interpolation safe.
        """
        with pytest.raises(ValueError, match="bare lowercase identifier"):
            PostgresChunkStore(pool, table=table)

    async def test_close_does_not_close_a_pool_it_does_not_own(
        self, store: PostgresChunkStore, pool: asyncpg.Pool[Any]
    ) -> None:
        await store.close()
        assert await pool.fetchval("SELECT 1") == 1

    async def test_connect_owns_and_closes_its_pool(self, pool: asyncpg.Pool[Any]) -> None:
        """`pool` is requested purely for its skip.

        This is the only test that builds a pool of its own, so without the
        dependency it is the only one that *fails* rather than skips when
        Postgres is absent. A skip guard is only honest if every test in the
        module is behind it.
        """
        owned = await PostgresChunkStore.connect(DSN, table=TABLE)
        await owned.ensure_schema()
        assert await owned.get(chunk_id("doc-1", "never stored"), uuid4()) is None
        await owned.close()

        with pytest.raises(Exception, match="closed"):
            await owned.get(chunk_id("doc-1", "never stored"), uuid4())
