"""The pgvector `VectorStore` adapter against the shared compliance suite.

Everything asserted about behaviour lives in `tests.compliance.vector_store`.
This module supplies a store and adds only what is specific to *this*
adapter -- schema, encoding fidelity, query plans and round-trip cost.

Start the backend deliberately::

    docker compose -f docker-compose.test.yml up -d postgres
    KG_COMPLIANCE_MAX_EXAMPLES=10 uv run pytest -m integration

`-m integration` is required: `addopts` excludes the marker so the commit gate
stays infra-free.

## Why the skip probe writes a row

`pytest.skip` when Postgres is absent is only honest if the probe proves the
server can *serve*, and for this adapter "serve" includes the `vector`
extension -- `pgvector/pgvector:pg16` ships the files, but a database that has
never run `CREATE EXTENSION` cannot store a vector. A TCP connect proves
neither. This repo has already paid for the weaker check once: the accuracy
suite probed Ollama's model listing, the model was listed but would not load,
and eight tests failed instead of skipping (BACKLOG B12). So the probe creates
the extension and round-trips one vector through a temporary table.

## Why each xdist worker gets its own table

BACKLOG B10f: the Neo4j integration suite wipes one shared database before
every test, so under `pytest-xdist` each worker destroys the others' data
mid-test -- 36 failures that say nothing about the code. Resetting a shared
`kg_vectors` between tests would reproduce it exactly.

The table name therefore carries `PYTEST_XDIST_WORKER`, so a worker truncates
only its own rows and the whole suite stays parallel-safe. This is cheaper
than the `xdist_group` B10f suggests for Neo4j and strictly better: it keeps
the tests parallel instead of serialising them onto one worker. It is
available here and not there only because Postgres allows as many tables as we
like, while Neo4j community allows one database.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.vector import VectorRecord
from redstring.vector.adapters.pgvector import PgVectorStore
from tests.compliance.vector_store import VectorStoreCompliance

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg

    from redstring.ports.vector_store import VectorStore

pytestmark = pytest.mark.integration

DSN = os.environ.get(
    "KG_TEST_POSTGRES_DSN", "postgresql://postgres:redstring@localhost:5434/redstring_test"
)

#: One table per xdist worker; see the module docstring. `gw0` and friends are
#: already valid bare identifiers, and the adapter rejects anything that is not.
TABLE = f"kg_vectors_test_{os.environ.get('PYTEST_XDIST_WORKER', 'main')}"

DIMENSION = VectorStoreCompliance.DIMENSION


async def _probe() -> asyncpg.Pool[Any] | None:
    """A connected pool, or `None` if pgvector cannot serve a trivial query."""
    try:
        import asyncpg
    except ImportError:  # pragma: no cover - asyncpg is a hard dependency
        return None

    try:
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
    except Exception:
        return None
    if pool is None:  # pragma: no cover - defensive; create_pool raises instead
        return None
    try:
        async with pool.acquire() as connection:
            # Not "the port answered": a server still starting accepts
            # connections, and one without the extension accepts them too and
            # then cannot store a single vector.
            await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await connection.execute(
                "CREATE TEMP TABLE _kg_probe (v vector(3)) ON COMMIT PRESERVE ROWS"
            )
            await connection.execute("INSERT INTO _kg_probe VALUES ('[1,2,3]')")
            stored = await connection.fetchval("SELECT v::real[] FROM _kg_probe")
            if list(stored) != [1.0, 2.0, 3.0]:
                raise RuntimeError(stored)
    except Exception:
        await pool.close()
        return None
    return pool


_schema_ready = False


@pytest.fixture
async def pool() -> AsyncIterator[asyncpg.Pool[Any]]:
    """A pool for one test, or skip it.

    Function-scoped for the same reason the Neo4j driver is: an asyncpg pool
    binds to the event loop that created it, and `asyncio_default_fixture_loop_scope`
    is `function`. One pool per *test* still means one pool for all of a
    property test's hypothesis examples, which is the case that matters.
    """
    global _schema_ready

    connected = await _probe()
    if connected is None:
        pytest.skip(
            f"Postgres with the `vector` extension is not serving at {DSN}. Start it with "
            f"`docker compose -f docker-compose.test.yml up -d postgres`."
        )
    if not _schema_ready:
        await PgVectorStore(connected, dimension=DIMENSION, table=TABLE).ensure_schema()
        _schema_ready = True
    try:
        yield connected
    finally:
        await connected.close()


async def _truncate(pool: asyncpg.Pool[Any]) -> None:
    """Empty this worker's table.

    The reset lives here, not on the adapter: "delete every tenant's rows" is a
    test affordance, and a production `VectorStore` should not offer one.
    `delete_by_tenant` is the port's bulk removal.
    """
    await pool.execute(f"TRUNCATE {TABLE}")


class TestPgVectorStore(VectorStoreCompliance):
    """The whole compliance suite, unchanged, against real pgvector."""

    @pytest.fixture(autouse=True)
    def _pool(self, pool: asyncpg.Pool[Any]) -> None:
        # Stashed on the instance because `new_store` takes no arguments: the
        # suite's contract is that an adapter supplies exactly one thing.
        self._shared_pool = pool

    async def new_store(self) -> VectorStore:
        await _truncate(self._shared_pool)
        return PgVectorStore(self._shared_pool, dimension=self.DIMENSION, table=TABLE)

    async def dispose(self, store: VectorStore) -> None:
        assert isinstance(store, PgVectorStore)
        await store.close()


class TestPgVectorSpecifics:
    """Behaviour the port does not specify, so the compliance suite cannot."""

    @pytest.fixture
    async def store(self, pool: asyncpg.Pool[Any]) -> AsyncIterator[PgVectorStore]:
        await _truncate(pool)
        built = PgVectorStore(pool, dimension=DIMENSION, table=TABLE)
        try:
            yield built
        finally:
            await built.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def test_ensure_schema_is_idempotent(self, store: PgVectorStore) -> None:
        await store.ensure_schema()
        await store.ensure_schema()

    async def test_the_primary_key_is_the_pair_not_the_entity_id(
        self, store: PgVectorStore, pool: asyncpg.Pool[Any]
    ) -> None:
        """Two tenants may hold the same entity id.

        A key on `entity_id` alone would make the second write replace the
        first, and that arrangement is what the isolation properties depend on
        most.
        """
        await store.ensure_schema()
        columns = await pool.fetch(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = $1::regclass AND i.indisprimary "
            "ORDER BY array_position(i.indkey, a.attnum)",
            TABLE,
        )
        assert [row["attname"] for row in columns] == ["tenant_id", "entity_id"]

    async def test_there_is_no_ann_index_on_the_embedding(
        self, store: PgVectorStore, pool: asyncpg.Pool[Any]
    ) -> None:
        """Deliberately absent. See the adapter's module docstring.

        An `hnsw` or `ivfflat` index here would let the planner take the `k`
        globally nearest rows and filter tenants afterwards, silently returning
        fewer than `k` for a tenant with genuine neighbours further down. This
        test exists so adding one is a decision rather than a drive-by
        optimisation: whoever adds it has to come here and argue with this
        docstring first.
        """
        await store.ensure_schema()
        methods = await pool.fetch(
            "SELECT am.amname FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_am am ON am.oid = c.relam "
            "WHERE i.indrelid = $1::regclass",
            TABLE,
        )
        assert {row["amname"] for row in methods} == {"btree"}

    async def test_a_table_of_another_dimension_is_rejected(
        self, store: PgVectorStore, pool: asyncpg.Pool[Any]
    ) -> None:
        """`vector(n)` bakes the dimension into the column type.

        Pointing a 768-dimension store at a 1024-dimension table would
        otherwise fail on the first insert, with a Postgres error naming
        neither the store nor the model.

        The dimension used is **768, not the suite's 8**, and both directions
        of mismatch are checked. Both details are load-bearing, and cosmic-ray
        found each:

        - `!=` replaced by `is not` survived at dimension 8, because CPython
          caches integers to 256. At 768 the mutant rejects a table that
          matches exactly -- every store would refuse to start.
        - `!=` replaced by `<` survived while only the too-small-table case
          was tested. A table *larger* than the store then passed silently and
          failed later on an insert, which is the error this check exists to
          replace.
        """
        table = f"{TABLE}_realistic"
        await pool.execute(f"DROP TABLE IF EXISTS {table}")
        await PgVectorStore(pool, dimension=768, table=table).ensure_schema()

        # The matching store starts, and says so by not raising.
        await PgVectorStore(pool, dimension=768, table=table).ensure_schema()

        for offered in (767, 769):
            with pytest.raises(DimensionMismatchError) as raised:
                await PgVectorStore(pool, dimension=offered, table=table).ensure_schema()
            assert raised.value.expected == 768
            assert raised.value.actual == offered

        await pool.execute(f"DROP TABLE {table}")

    async def test_ensure_schema_creates_the_table_from_nothing(
        self, pool: asyncpg.Pool[Any]
    ) -> None:
        """The DDL must actually run.

        Every other test in this module works against a table an earlier run
        already created, so **the schema statements could do nothing at all
        and nothing would notice** -- cosmic-ray proved it by replacing the
        loop's iterable with `[]`, which survived the whole suite. The only
        test that can see this is one that starts from no table.
        """
        table = f"{TABLE}_fresh"
        await pool.execute(f"DROP TABLE IF EXISTS {table}")
        assert not await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", table)

        store = PgVectorStore(pool, dimension=DIMENSION, table=table)
        await store.ensure_schema()

        assert await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", table)
        # Usable, not merely present: the column types and the primary key
        # have to be right or the round trip below fails.
        entity_id, tenant = uuid4(), uuid4()
        vector = [1.0, *([0.0] * (DIMENSION - 1))]
        await store.upsert(entity_id, vector, tenant, metadata={"entity_type": "person"})
        found = await store.get(entity_id, tenant)
        assert found is not None
        assert found.vector == vector
        matches = await store.search(vector, tenant, entity_types=["person"])
        assert [match.entity_id for match in matches] == [entity_id]

        await pool.execute(f"DROP TABLE {table}")

    # ------------------------------------------------------------------
    # Query plans
    #
    # Slice 4's tenant-scoped Neo4j reads planned as a full scan across every
    # tenant despite filtering on `tenant_id`: correct results, catastrophic
    # cost, invisible to any behavioural test. pgvector has the identical
    # trap, so the plan is asserted rather than assumed.
    # ------------------------------------------------------------------

    async def test_a_tenant_scoped_search_seeks_rather_than_scanning_the_table(
        self, store: PgVectorStore, pool: asyncpg.Pool[Any]
    ) -> None:
        """A read for one tenant must not cost the whole table.

        The results are identical either way, so only the plan can see this.
        The dataset is large enough (20k rows over 400 tenants) that the
        planner has a real choice to make -- on a table of ten rows a
        sequential scan is genuinely correct and the assertion would prove
        nothing about production.
        """
        tenants = [uuid4() for _ in range(400)]
        await store.upsert_many(
            [
                VectorRecord(
                    entity_id=uuid4(),
                    tenant_id=tenant,
                    vector=[float(index % 7) + 1.0, *([0.0] * (DIMENSION - 1))],
                )
                for tenant in tenants
                for index in range(50)
            ]
        )
        await pool.execute(f"ANALYZE {TABLE}")

        plan = await self._explain(store, pool, tenants[0])

        assert "Seq Scan" not in plan, (
            f"a tenant-scoped search reads every row of every tenant:\n{plan}\n"
            f"The primary key leads with tenant_id; the query must seek on it."
        )
        assert "Index" in plan

    async def test_the_search_plan_filters_before_it_limits(
        self, store: PgVectorStore, pool: asyncpg.Pool[Any]
    ) -> None:
        """`Limit` must sit above the filter, never below it.

        This is the port's "filters are applied before `k`" rule read off the
        plan rather than inferred from results. It is what an ANN index would
        break: the planner would push the ordering into an index scan that
        knows nothing about `tenant_id` or `entity_type` and cut to `k` first.

        The table is **seeded rather than left at the single row the earlier
        version used**, and `ANALYZE`d. On a one-row relation `assert
        conditions` leans on the planner still preferring the primary key over
        a sequential scan of nothing, which is the same order-sensitivity that
        already produced one bug here: it is not that the assertion was wrong,
        it is that on an empty table it was not testing anything the planner
        had to decide.
        """
        tenant = uuid4()
        await store.upsert_many(
            [
                VectorRecord(
                    entity_id=uuid4(),
                    tenant_id=uuid4() if index % 5 else tenant,
                    vector=[float(index % 7) + 1.0, *([0.0] * (DIMENSION - 1))],
                    metadata={"entity_type": "person" if index % 2 else "place"},
                )
                for index in range(500)
            ]
        )
        await pool.execute(f"ANALYZE {TABLE}")

        plan = await self._explain(store, pool, tenant, entity_types=["person"])

        assert plan.splitlines()[0].strip() == "Limit", (
            f"the top of the plan is not the Limit, so something runs *after* "
            f"`k` has been taken:\n{plan}"
        )
        # The tenant predicate is resolved by an index, and **both** filters
        # are evaluated below the Limit. A post-filter would appear above it
        # instead, free to discard rows the Limit had already committed to.
        #
        # Deliberately not asserted: *which* node evaluates `entity_type`. On
        # a well-populated table the planner folds it into the same `Index
        # Cond`; on a nearly empty one it leaves it as a `Filter` under the
        # scan. Both satisfy the contract, and pinning the richer plan made
        # this test pass or fail according to whether a 20k-row test had run
        # before it -- an order dependency, which is a bug in the test.
        conditions = [line for line in plan.splitlines() if "Index Cond" in line]
        assert conditions, f"no index condition at all:\n{plan}"
        assert "tenant_id" in conditions[0]
        below_limit = plan.split("Limit", 1)[1]
        assert "tenant_id" in below_limit
        assert "entity_type" in below_limit

    @staticmethod
    async def _explain(
        store: PgVectorStore,
        pool: asyncpg.Pool[Any],
        tenant: Any,
        *,
        entity_types: list[str] | None = None,
    ) -> str:
        """The text plan for the adapter's own search statement.

        The SQL comes from the adapter rather than being restated here, so the
        plan asserted is the plan the port actually runs. `ANALYZE false`
        keeps this a planning question, not a timing one.
        """
        query = store._search_sql()
        rows = await pool.fetch(
            "EXPLAIN (ANALYZE false, COSTS false, VERBOSE true) " + query,
            tenant,
            "[" + ",".join(["1.0"] * DIMENSION) + "]",
            entity_types is None,
            entity_types or [],
            None,
            10,
        )
        return "\n".join(row["QUERY PLAN"] for row in rows)

    # ------------------------------------------------------------------
    # Round-trip cost
    #
    # `upsert_many` exists so an embedding batch is one statement rather than
    # thousands. That is a property of this adapter, not of the port, so the
    # compliance suite cannot assert it -- and it is the exact regression a
    # later refactor would introduce silently.
    # ------------------------------------------------------------------

    async def test_upsert_many_is_one_statement(
        self, store: PgVectorStore, pool: asyncpg.Pool[Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tenant = uuid4()
        records = [
            VectorRecord(
                entity_id=uuid4(),
                tenant_id=tenant,
                vector=[float(i + 1), *([0.0] * (DIMENSION - 1))],
            )
            for i in range(250)
        ]
        executed: list[str] = []
        original = type(pool).execute

        async def counting(self: Any, query: str, *args: Any, **kwargs: Any) -> Any:
            executed.append(query)
            return await original(self, query, *args, **kwargs)

        monkeypatch.setattr(type(pool), "execute", counting)

        await store.upsert_many(records)

        assert len(executed) == 1, f"{len(executed)} statements for 250 rows: this is a loop"
        assert len(await store.search([1.0, *([0.0] * (DIMENSION - 1))], tenant, k=250)) == 250

    # ------------------------------------------------------------------
    # Encoding fidelity
    #
    # `vector` is float4 and `metadata` is jsonb. The compliance suite covers
    # both through generated values; these pin the specific shapes a round
    # trip is most likely to flatten, so a failure names the shape instead of
    # arriving as a shrunk counterexample.
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
            # The key the `entity_type` column is derived from, holding
            # something that is not a type name.
            pytest.param({"entity_type": None}, id="null-entity-type"),
            pytest.param({"entity_type": 7}, id="non-string-entity-type"),
        ],
    )
    async def test_metadata_round_trips_exactly(
        self, store: PgVectorStore, metadata: dict[str, Any]
    ) -> None:
        tenant, entity_id = uuid4(), uuid4()
        vector = [1.0, *([0.0] * (DIMENSION - 1))]
        await store.upsert(entity_id, vector, tenant, metadata=metadata)

        found = await store.get(entity_id, tenant)
        assert found is not None
        assert found.metadata == metadata
        assert json.dumps(found.metadata, sort_keys=True) == json.dumps(metadata, sort_keys=True)

    async def test_a_non_string_entity_type_never_matches_a_filter(
        self, store: PgVectorStore
    ) -> None:
        """The column is `text`; coercing `7` to `"7"` would invent a match the
        in-memory adapter would not make."""
        tenant, entity_id = uuid4(), uuid4()
        await store.upsert(
            entity_id, [1.0, *([0.0] * (DIMENSION - 1))], tenant, metadata={"entity_type": 7}
        )

        query = [1.0, *([0.0] * (DIMENSION - 1))]
        assert await store.search(query, tenant, k=10, entity_types=["7"]) == []
        assert len(await store.search(query, tenant, k=10)) == 1

    async def test_negative_and_fractional_components_survive(self, store: PgVectorStore) -> None:
        tenant, entity_id = uuid4(), uuid4()
        # Every value is exactly representable in float32, which is what the
        # port promises and all it promises.
        vector = [-1.5, 0.25, 0.0, 1024.0, -0.0625, 3.5, -7.0, 0.5][:DIMENSION]
        await store.upsert(entity_id, vector, tenant)

        found = await store.get(entity_id, tenant)
        assert found is not None
        assert found.vector == vector

    # ------------------------------------------------------------------
    # Connection ownership
    # ------------------------------------------------------------------

    async def test_close_does_not_close_a_pool_it_does_not_own(
        self, store: PgVectorStore, pool: asyncpg.Pool[Any]
    ) -> None:
        """The suite disposes a store per hypothesis example.

        Closing an injected pool would take the whole session's connections
        down with the first example.
        """
        await store.close()
        assert await pool.fetchval("SELECT 1") == 1

    async def test_connect_owns_and_closes_its_pool(self, pool: asyncpg.Pool[Any]) -> None:
        """`pool` is requested purely for its skip.

        This is the only test that builds a pool of its own, so without the
        dependency it is the only one that *fails* rather than skips when
        Postgres is absent. A skip guard is only honest if every test in the
        module is behind it.
        """
        owned = await PgVectorStore.connect(DSN, dimension=DIMENSION, table=TABLE)
        await owned.ensure_schema()
        assert await owned.search([1.0, *([0.0] * (DIMENSION - 1))], uuid4()) == []
        await owned.close()

        with pytest.raises(Exception, match="closed"):
            await owned.search([1.0, *([0.0] * (DIMENSION - 1))], uuid4())
