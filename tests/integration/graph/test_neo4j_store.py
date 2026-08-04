"""The Neo4j `GraphStore` adapter against the shared compliance suite.

Everything asserted about behaviour lives in `tests.compliance.graph_store`.
This module supplies a store and adds only what is specific to *this*
adapter -- schema creation, encoding fidelity, and connection handling.

Start the backend deliberately::

    docker compose -f docker-compose.test.yml up -d neo4j
    KG_COMPLIANCE_MAX_EXAMPLES=10 uv run pytest -m integration

`-m integration` is required: `addopts` excludes the marker so the commit gate
stays infra-free.

## Why the skip probe runs a query

`pytest.skip` when Neo4j is absent is only honest if the probe proves the
server can *serve*. A TCP connect succeeds against a Neo4j that is still
recovering its store files, and against one whose credentials are wrong. This
repo has already paid for the weaker check once: the accuracy suite probed
Ollama's model listing, the model was listed but would not load, and eight
tests failed instead of skipping (BACKLOG B12). So the probe runs `RETURN 1`
and requires the answer to be 1.

## Why the database is wiped rather than scoped to a fresh tenant

The natural cheap reset is a random tenant per store, but the compliance suite
generates its own tenant ids -- `new_store()` never learns them, so it cannot
scope to one. The reset is therefore a real `MATCH (n) DETACH DELETE n`, which
on a database holding a handful of nodes costs about a millisecond. One driver
is shared across every example of a test, so `dispose` must not close it --
see `Neo4jGraphStore.close`, which only closes a driver the store created.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from kg_builder.domain.entity import Entity, ExtractionMethod
from kg_builder.domain.exceptions import MissingEntityError
from kg_builder.graph.adapters.neo4j import Neo4jGraphStore
from tests.compliance.graph_store import GraphStoreCompliance

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from neo4j import AsyncDriver

    from kg_builder.ports.graph_store import GraphStore

pytestmark = pytest.mark.integration

NEO4J_URI = os.environ.get("KG_TEST_NEO4J_URI", "bolt://localhost:7688")
NEO4J_AUTH = (
    os.environ.get("KG_TEST_NEO4J_USER", "neo4j"),
    os.environ.get("KG_TEST_NEO4J_PASSWORD", "kgbuilder"),
)


async def _probe() -> AsyncDriver | None:
    """A connected driver, or `None` if Neo4j cannot serve a trivial query."""
    try:
        from neo4j import AsyncGraphDatabase
    except ImportError:  # pragma: no cover - the neo4j extra is installed in dev
        return None

    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    try:
        # Not `verify_connectivity()`: that authenticates and returns, which a
        # server still recovering its store files also does. Only a query
        # answered correctly proves it can serve.
        async with driver.session() as session:
            result = await session.run("RETURN 1 AS one")
            record = await result.single()
        if record is None or record["one"] != 1:
            await driver.close()
            return None
    except Exception:
        await driver.close()
        return None
    return driver


async def _wipe(driver: AsyncDriver) -> None:
    """Empty the test database.

    The wipe lives here, not on the adapter: "delete everything regardless of
    tenant" is a test affordance, and a production `GraphStore` should not
    offer one. `delete_by_tenant` is the port's bulk removal.
    """
    async with driver.session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


#: Set once the schema has been created. `ensure_schema` is idempotent, so
#: this is an optimisation rather than a correctness device -- five DDL
#: statements per test is pure overhead once the first test has run.
_schema_ready = False


@pytest.fixture
async def neo4j_driver() -> AsyncIterator[AsyncDriver]:
    """A driver for one test, or skip it.

    Function-scoped, not session-scoped, because the neo4j async driver binds
    to the event loop that created it and `asyncio_default_fixture_loop_scope`
    is `function`. A session-scoped driver would be reused across loops, which
    fails as a `ScopeMismatch` at best and as a wrong-loop hang at worst. One
    driver per *test* still means one driver for all of a property test's
    hypothesis examples, which is the case that matters.
    """
    global _schema_ready

    driver = await _probe()
    if driver is None:
        pytest.skip(
            f"Neo4j at {NEO4J_URI} did not answer `RETURN 1`. Start it with "
            f"`docker compose -f docker-compose.test.yml up -d neo4j`."
        )
    if not _schema_ready:
        await Neo4jGraphStore(driver).ensure_schema()
        _schema_ready = True
    try:
        yield driver
    finally:
        await driver.close()


class TestNeo4jStore(GraphStoreCompliance):
    """The whole compliance suite, unchanged, against real Neo4j."""

    @pytest.fixture(autouse=True)
    def _driver(self, neo4j_driver: AsyncDriver) -> None:
        # Stashed on the instance because `new_store` takes no arguments: the
        # suite's contract is that an adapter supplies exactly one thing.
        self._shared_driver = neo4j_driver

    async def new_store(self) -> GraphStore:
        await _wipe(self._shared_driver)
        return Neo4jGraphStore(self._shared_driver)

    async def dispose(self, store: GraphStore) -> None:
        assert isinstance(store, Neo4jGraphStore)
        await store.close()


class TestNeo4jSpecifics:
    """Behaviour the port does not specify, so the compliance suite cannot."""

    @pytest.fixture
    async def store(self, neo4j_driver: AsyncDriver) -> AsyncIterator[Neo4jGraphStore]:
        await _wipe(neo4j_driver)
        store = Neo4jGraphStore(neo4j_driver)
        try:
            yield store
        finally:
            await store.close()

    async def test_ensure_schema_is_idempotent(self, store: Neo4jGraphStore) -> None:
        """Called on every connect, so running twice must not raise."""
        await store.ensure_schema()
        await store.ensure_schema()

    async def test_ensure_schema_creates_the_uniqueness_constraint(
        self, store: Neo4jGraphStore, neo4j_driver: AsyncDriver
    ) -> None:
        await store.ensure_schema()
        async with neo4j_driver.session() as session:
            result = await session.run("SHOW CONSTRAINTS YIELD name, properties")
            constraints = {record["name"]: record["properties"] async for record in result}
        assert constraints.get("entity_tenant_id_unique") == ["tenant_id", "id"]

    async def test_ensure_schema_creates_the_lookup_indexes(
        self, store: Neo4jGraphStore, neo4j_driver: AsyncDriver
    ) -> None:
        """`find_entities` and `find_by_blocking_key` scan without these."""
        await store.ensure_schema()
        async with neo4j_driver.session() as session:
            result = await session.run("SHOW INDEXES YIELD name, properties")
            indexes = {record["name"]: record["properties"] async for record in result}
        assert indexes["entity_tenant_normalized_name"] == ["tenant_id", "normalized_name"]
        assert indexes["entity_tenant_type"] == ["tenant_id", "entity_type"]
        assert indexes["relationship_tenant_id"] == ["tenant_id", "id"]
        # Deliberately absent: a range index over a list property cannot serve
        # `$key IN e.blocking_keys`, so one would cost writes and buy nothing.
        assert "entity_tenant_blocking_keys" not in indexes

    @pytest.mark.parametrize(
        ("method", "call"),
        [
            ("find_entities", lambda store, tenant: store.find_entities(tenant)),
            (
                "find_by_blocking_key",
                lambda store, tenant: store.find_by_blocking_key("A430", tenant),
            ),
            (
                "find_by_blocking_keys",
                lambda store, tenant: store.find_by_blocking_keys(["A430"], tenant),
            ),
        ],
    )
    async def test_tenant_scoped_reads_seek_rather_than_scan_the_label(
        self,
        store: Neo4jGraphStore,
        neo4j_driver: AsyncDriver,
        monkeypatch: pytest.MonkeyPatch,
        method: str,
        call: Any,
    ) -> None:
        """A read for one tenant must not cost the whole database.

        These three have no indexed predicate of their own -- no name, no
        type, no cursor -- and Neo4j plans `MATCH (e:Entity {tenant_id: $t})`
        as a whole-label scan unless the query also mentions `e.id`. The
        results are identical either way, so no behavioural test can see this;
        only the plan can. Measured on 5000 entities across 100 tenants, the
        difference was `NodeByLabelScan` versus `NodeUniqueIndexSeek`.
        """
        with _counting(store, monkeypatch) as queries:
            await call(store, uuid4())

        assert len(queries) == 1
        async with neo4j_driver.session() as session:
            result = await session.run(
                "EXPLAIN " + queries.queries[0],
                tenant_id=str(uuid4()),
                key="A430",
                keys=["A430"],
            )
            plan = (await result.consume()).plan

        assert _operators(plan) & {"NodeUniqueIndexSeek", "NodeIndexSeek"}, (
            f"{method} plans as {sorted(_operators(plan))}: it scans every "
            f"entity of every tenant. Add the `_TENANT_SEEK` predicate."
        )
        assert "NodeByLabelScan" not in _operators(plan)

    async def test_the_uniqueness_constraint_is_composite_not_id_alone(
        self, store: Neo4jGraphStore
    ) -> None:
        """Two tenants may hold the same entity id.

        A constraint on `id` alone would make the second write fail, and it is
        the arrangement the isolation properties depend on most.
        """
        await store.ensure_schema()
        entity_id = uuid4()
        for tenant in (uuid4(), uuid4()):
            await store.upsert_entity(_entity(tenant=tenant, id=entity_id))

    async def test_close_does_not_close_a_driver_it_does_not_own(
        self, store: Neo4jGraphStore, neo4j_driver: AsyncDriver
    ) -> None:
        """The suite disposes a store per hypothesis example.

        Closing an injected driver would take the whole session's pool down
        with the first example.
        """
        await store.close()
        async with neo4j_driver.session() as session:
            result = await session.run("RETURN 1 AS one")
            assert (await result.single())["one"] == 1  # type: ignore[index]

    async def test_connect_owns_and_closes_its_driver(self, neo4j_driver: AsyncDriver) -> None:
        """`neo4j_driver` is requested purely for its skip.

        This is the only test that builds a driver of its own, so without the
        dependency it is the only one that *fails* rather than skips when
        Neo4j is absent -- which is how it was caught: 102 skipped, 1 failed
        with `ServiceUnavailable`. A skip guard is only honest if every test
        in the module is behind it.
        """
        store = Neo4jGraphStore.connect(NEO4J_URI, auth=NEO4J_AUTH)
        await store.ensure_schema()
        assert await store.find_entities(uuid4()) == []
        await store.close()

        # The 5.x driver warns rather than raising on use after close, so the
        # warning is the observable proof that `close()` closed it. Asserting
        # `pytest.raises` here fails with `DID NOT RAISE`; when a future driver
        # promotes this to an error, `pytest.warns` will fail loudly and this
        # test becomes the place to notice.
        with pytest.warns(DeprecationWarning, match="closed"):
            await store.find_entities(uuid4())

    # ------------------------------------------------------------------
    # Encoding fidelity
    #
    # Neo4j property values are primitives or homogeneous arrays, so
    # `properties`, `external_ids` and `temporal` are JSON columns. The
    # compliance suite covers this through generated entities; these pin the
    # specific shapes a JSON round trip is most likely to flatten, so a
    # failure names the shape instead of arriving as a shrunk counterexample.
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "properties",
        [
            pytest.param({}, id="empty"),
            pytest.param({"nested": {"deep": {"deeper": [1, 2, {"x": None}]}}}, id="nested"),
            pytest.param({"empty_dict": {}, "empty_list": []}, id="empty-containers"),
            # A Neo4j array must be homogeneous, so this cannot be stored as a
            # native property at all.
            pytest.param({"mixed": [1, "two", None, True, {"k": []}]}, id="heterogeneous-array"),
            # `True` and `1` are distinct in Python and equal under `==` in a
            # naive comparison; JSON keeps them apart, a bool-to-int coercion
            # would not.
            pytest.param({"bool": True, "int": 1, "float": 1.0}, id="bool-is-not-int"),
            # Beyond int64: Neo4j cannot hold this as an integer property.
            pytest.param({"big": 2**70}, id="bignum"),
            pytest.param({"": "empty key", "unicode": "é中\U0001f600"}, id="odd-keys"),
        ],
    )
    async def test_properties_round_trip_exactly(
        self, store: Neo4jGraphStore, properties: dict[str, Any]
    ) -> None:
        tenant = uuid4()
        entity = _entity(tenant=tenant, properties=properties)
        await store.upsert_entity(entity)

        found = await store.get_entity(entity.id, tenant)
        assert found is not None
        assert found.properties == properties
        # `==` on a dict does not distinguish True from 1, so the types are
        # checked directly too.
        assert [type(v) for v in found.properties.values()] == [
            type(v) for v in properties.values()
        ]

    async def test_empty_and_absent_are_distinguishable(self, store: Neo4jGraphStore) -> None:
        """`None` and an empty container are different values, not both "unset".

        Setting a Neo4j property to null *removes* it, so an adapter that maps
        `frozenset()` and `None` onto the same absence loses the distinction.
        """
        tenant = uuid4()
        absent = _entity(tenant=tenant, blocking_keys=None, external_ids={}, properties={})
        empty = _entity(tenant=tenant, blocking_keys=frozenset(), external_ids={}, properties={})
        await store.upsert_entities([absent, empty])

        assert (await store.get_entity(absent.id, tenant)).blocking_keys is None  # type: ignore[union-attr]
        assert (await store.get_entity(empty.id, tenant)).blocking_keys == frozenset()  # type: ignore[union-attr]

    async def test_optional_scalars_survive_being_none(self, store: Neo4jGraphStore) -> None:
        """An absent Neo4j property must decode to `None`, not raise."""
        tenant = uuid4()
        entity = _entity(tenant=tenant)
        assert entity.description is None
        await store.upsert_entity(entity)

        assert await store.get_entity(entity.id, tenant) == entity

    async def test_an_empty_string_is_not_none(self, store: Neo4jGraphStore) -> None:
        tenant = uuid4()
        entity = _entity(tenant=tenant, description="", source_text="")
        await store.upsert_entity(entity)

        found = await store.get_entity(entity.id, tenant)
        assert found is not None
        assert found.description == ""
        assert found.source_text == ""

    # ------------------------------------------------------------------
    # Round-trip cost
    #
    # The batch methods exist to avoid N+1 Cypher. That they issue a bounded
    # number of queries is a property of this adapter, not of the port, so the
    # compliance suite cannot assert it -- and it is the exact regression a
    # later refactor would introduce silently.
    # ------------------------------------------------------------------

    async def test_upsert_entities_is_a_bounded_number_of_round_trips(
        self, store: Neo4jGraphStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Three: the entities, the stale key edges, the new key edges.

        It was one until B10b made blocking keys nodes. What matters is not the
        number but that it **does not grow with the batch**, which is what the
        two sizes here check -- an implementation that looped per entity would
        satisfy any fixed count chosen for a single size.

        The stale-edge delete is unconditional and so is always one of the
        three; the create is skipped when nothing in the batch carries keys,
        which the case below pins.
        """
        tenant = uuid4()
        small = [_entity(tenant=tenant, blocking_keys=frozenset({"A430"})) for _ in range(5)]
        large = [_entity(tenant=tenant, blocking_keys=frozenset({"A430"})) for _ in range(50)]

        with _counting(store, monkeypatch) as few:
            await store.upsert_entities(small)
        with _counting(store, monkeypatch) as many:
            await store.upsert_entities(large)

        assert len(few) == len(many) == 3

    async def test_upserting_entities_with_no_keys_skips_the_key_write(
        self, store: Neo4jGraphStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The delete still runs. It is what clears an entity's *previous*
        keys, and skipping it for a keyless batch is exactly the stale-key bug
        B10b warns about."""
        tenant = uuid4()

        with _counting(store, monkeypatch) as queries:
            await store.upsert_entities([_entity(tenant=tenant) for _ in range(5)])

        assert len(queries) == 2

    async def test_upsert_relationships_is_a_bounded_number_of_round_trips(
        self, store: Neo4jGraphStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two: one to prove no endpoint is dangling, one to write.

        The count must not grow with the batch. Validation is separate because
        `MissingEntityError` names *which* endpoint is missing, which a write
        query cannot report while also writing.
        """
        tenant = uuid4()
        entities = [_entity(tenant=tenant) for _ in range(20)]
        await store.upsert_entities(entities)
        edges = [
            _relationship(tenant, source=entities[i].id, target=entities[i + 1].id)
            for i in range(19)
        ]

        with _counting(store, monkeypatch) as queries:
            await store.upsert_relationships(edges)

        assert len(queries) == 2

    async def test_the_write_raises_when_it_matches_fewer_rows_than_it_was_given(
        self, store: Neo4jGraphStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Write-or-raise has to hold across the gap between the two queries.

        `_reject_dangling` and the write are separate implicit transactions, so
        an endpoint deleted between them makes the write's `MATCH` match
        nothing -- and Cypher drops that row silently. A single-threaded test
        cannot produce the interleaving, so it produces the *state* the
        interleaving leaves: the check passes (stubbed out for its first call)
        and the endpoints are genuinely absent when the write runs.

        This is the half of the fix a mock cannot cover: it proves the real
        Cypher actually returns a row per edge written, which is what makes a
        short write detectable at all.
        """
        tenant = uuid4()
        source, target = _entity(tenant=tenant), _entity(tenant=tenant)
        # Deliberately not written: the endpoints do not exist.
        real = store._reject_dangling
        calls = 0

        async def skip_the_first_check(relationships: Any) -> None:
            nonlocal calls
            calls += 1
            if calls > 1:
                await real(relationships)

        monkeypatch.setattr(store, "_reject_dangling", skip_the_first_check)

        with pytest.raises(MissingEntityError) as raised:
            await store.upsert_relationships(
                [_relationship(tenant, source=source.id, target=target.id)]
            )

        # The re-check still names which endpoint is missing, so the error a
        # caller sees is the same one an up-front dangling edge produces.
        assert raised.value.entity_id == source.id
        assert await store.get_relationships(source.id, tenant) == []

    async def test_batch_reads_are_one_round_trip_each(
        self, store: Neo4jGraphStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tenant = uuid4()
        entities = [_entity(tenant=tenant, blocking_keys=frozenset({f"k{i}"})) for i in range(10)]
        await store.upsert_entities(entities)
        ids = [e.id for e in entities]

        for call in (
            lambda: store.get_entities(ids, tenant),
            lambda: store.find_by_blocking_keys([f"k{i}" for i in range(10)], tenant),
            lambda: store.get_relationships_for(ids, tenant),
        ):
            with _counting(store, monkeypatch) as queries:
                await call()
            assert len(queries) == 1

    async def test_neighbors_is_one_query_regardless_of_depth(
        self, store: Neo4jGraphStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A variable-length path, not `depth` rounds of expansion."""
        tenant = uuid4()
        chain = [_entity(tenant=tenant) for _ in range(6)]
        await store.upsert_entities(chain)
        await store.upsert_relationships(
            [_relationship(tenant, source=chain[i].id, target=chain[i + 1].id) for i in range(5)]
        )

        with _counting(store, monkeypatch) as queries:
            found = await store.neighbors(chain[0].id, tenant, depth=5)

        assert len(queries) == 1
        assert len(found) == 5

    async def test_the_traversal_depth_is_not_string_interpolated_from_a_caller(
        self, store: Neo4jGraphStore
    ) -> None:
        """`depth` reaches Cypher as text, so it must be provably an integer.

        A variable-length pattern cannot take a parameter, which is the one
        place in this adapter where a value is formatted into a query string.
        """
        with pytest.raises(TypeError, match="depth"):
            await store.neighbors(uuid4(), uuid4(), depth="1 OR 1=1")  # type: ignore[arg-type]

    async def test_a_bool_is_not_accepted_as_a_depth(self, store: Neo4jGraphStore) -> None:
        """`bool` is an `int` subclass, and `True` would format as `True`."""
        with pytest.raises(TypeError, match="depth"):
            await store.neighbors(uuid4(), uuid4(), depth=True)  # type: ignore[arg-type]


def _operators(plan: Any) -> set[str]:
    """Every operator in a Neo4j query plan, flattened."""
    # Neo4j 5 qualifies operators with the database name -- "NodeIndexSeek@neo4j".
    found = {plan["operatorType"].split("@")[0]}
    for child in plan.get("children", ()):
        found |= _operators(child)
    return found


class _QueryLog:
    """Records the Cypher a store issues, without changing what it does."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def __len__(self) -> int:
        return len(self.queries)


def _counting(store: Neo4jGraphStore, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Context manager yielding a log of the queries `store` runs inside it."""
    from contextlib import contextmanager

    @contextmanager
    def _cm() -> Any:
        log = _QueryLog()
        original = store._run

        async def counting(query: str, /, **parameters: Any) -> Any:
            log.queries.append(query)
            return await original(query, **parameters)

        monkeypatch.setattr(store, "_run", counting)
        try:
            yield log
        finally:
            monkeypatch.undo()

    return _cm()


def _entity(*, tenant: Any, **overrides: Any) -> Entity:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "tenant_id": tenant,
        "name": "Ada Lovelace",
        "normalized_name": "ada lovelace",
        "entity_type": "person",
        "extraction_method": ExtractionMethod.MANUAL,
        "confidence": 1.0,
    }
    fields.update(overrides)
    return Entity(**fields)


def _relationship(tenant: Any, *, source: Any, target: Any) -> Any:
    from kg_builder.domain.relationship import Relationship

    return Relationship(
        id=uuid4(),
        tenant_id=tenant,
        source_entity_id=source,
        target_entity_id=target,
        relationship_type="knows",
        confidence=1.0,
    )
