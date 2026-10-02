"""Validation, guard clause, encoding, and deduplication wiring tests for pgvector."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from redstring.domain.exceptions import DimensionMismatchError
from redstring.domain.vector import VectorRecord
from redstring.ports.vector_store import entity_type_of
from redstring.vector.adapters.pgvector import deduplicate, encode_vector

from .conftest import DIMENSION, make_store, make_vector


class TestConstruction:
    def test_dimension_must_be_positive(self):
        for bad in (0, -1):
            with pytest.raises(ValueError, match="dimension"):
                make_store(dimension=bad)

    def test_the_dimension_is_reported(self):
        assert make_store(dimension=768).dimension == 768

    def test_a_dimension_of_one_is_legal(self):
        """Degenerate but permitted: the port says positive, not 'more than one'."""
        assert make_store(dimension=1).dimension == 1

    async def test_a_correct_length_is_accepted_at_a_realistic_dimension(self):
        store = make_store(dimension=768)
        with pytest.raises(AssertionError, match="reached the database"):
            await store.upsert(uuid4(), [0.0] * 767 + [1.0], uuid4())

    @pytest.mark.parametrize(
        "table",
        [
            pytest.param("kg vectors", id="space"),
            pytest.param("public.kg_vectors", id="schema-qualified"),
            pytest.param('kg_vectors"; DROP TABLE users; --', id="injection"),
            pytest.param("KgVectors", id="uppercase"),
            pytest.param("9lives", id="leading-digit"),
            pytest.param("", id="empty"),
            pytest.param("x" * 64, id="too-long"),
        ],
    )
    def test_a_table_name_that_is_not_a_bare_identifier_is_rejected(self, table: str):
        with pytest.raises(ValueError, match="identifier"):
            make_store(table=table)

    @pytest.mark.parametrize("table", ["kg_vectors", "_v", "kg_vectors_test_gw0", "x" * 63])
    def test_a_bare_identifier_is_accepted(self, table: str):
        assert make_store(table=table).table == table

    def test_the_table_name_reaches_every_statement(self):
        store = make_store(table="kg_vectors_test_gw3")
        statements = [
            *store._schema_statements(),
            store._insert_sql(),
            store._search_sql(),
        ]
        for statement in statements:
            assert "kg_vectors_test_gw3" in statement
            assert "kg_vectors " not in statement.replace("kg_vectors_test_gw3", "")


class TestGuardsRunBeforeAnyIO:
    """Each of these reaches the database if the guard is removed."""

    async def test_upsert_rejects_the_wrong_dimension(self):
        with pytest.raises(DimensionMismatchError) as raised:
            await make_store().upsert(uuid4(), [1.0, 2.0], uuid4())
        assert raised.value.expected == DIMENSION
        assert raised.value.actual == 2

    async def test_upsert_many_rejects_the_wrong_dimension(self):
        record = VectorRecord(entity_id=uuid4(), tenant_id=uuid4(), vector=[1.0, 2.0])
        with pytest.raises(DimensionMismatchError):
            await make_store().upsert_many([record])

    async def test_search_rejects_the_wrong_dimension(self):
        with pytest.raises(DimensionMismatchError):
            await make_store().search([1.0, 2.0], uuid4())

    async def test_zero_vectors_are_rejected(self):
        zeroes = [0.0] * DIMENSION
        with pytest.raises(ValueError, match="zero"):
            await make_store().upsert(uuid4(), zeroes, uuid4())
        with pytest.raises(ValueError, match="zero"):
            await make_store().search(zeroes, uuid4())

    async def test_a_negative_k_is_rejected(self):
        with pytest.raises(ValueError, match="k"):
            await make_store().search(make_vector(), uuid4(), k=-1)

    async def test_k_zero_answers_without_asking(self):
        assert await make_store().search(make_vector(), uuid4(), k=0) == []

    async def test_an_empty_batch_writes_nothing(self):
        await make_store().upsert_many([])


class TestEncoding:
    def test_a_vector_renders_as_pgvectors_input_form(self):
        assert encode_vector([1.0, -2.5, 0.0]) == "[1.0,-2.5,0.0]"

    def test_encoding_does_not_shorten_a_float(self):
        awkward = 0.1 + 0.2
        assert float(encode_vector([awkward])[1:-1]) == awkward

    def test_integers_are_rendered_as_floats(self):
        assert encode_vector([1, 2]) == "[1.0,2.0]"

    def test_an_empty_vector_is_representable(self):
        assert encode_vector([]) == "[]"

    def test_entity_type_is_taken_from_the_metadata(self):
        assert entity_type_of({"entity_type": "person"}) == "person"

    @pytest.mark.parametrize(
        "metadata",
        [
            pytest.param({}, id="absent"),
            pytest.param({"entity_type": None}, id="null"),
            pytest.param({"entity_type": 7}, id="int"),
            pytest.param({"entity_type": True}, id="bool"),
            pytest.param({"entity_type": ["person"]}, id="list"),
            pytest.param({"Entity_Type": "person"}, id="wrong-case"),
        ],
    )
    def test_a_non_string_entity_type_becomes_null(self, metadata: dict[str, Any]):
        assert entity_type_of(metadata) is None

    def test_the_metadata_key_is_the_ports_constant(self):
        from redstring.ports.vector_store import ENTITY_TYPE_KEY

        assert entity_type_of({ENTITY_TYPE_KEY: "person"}) == "person"


class TestDeduplicate:
    """`ON CONFLICT DO UPDATE` raises if one statement touches a row twice."""

    def test_a_repeated_key_keeps_the_last(self):
        entity_id, tenant = uuid4(), uuid4()

        def record(value: int) -> VectorRecord:
            return VectorRecord(
                entity_id=entity_id, tenant_id=tenant, vector=[float(value)], metadata={"n": value}
            )

        first, second = record(1), record(2)

        assert deduplicate([first, second]) == [second]

    def test_the_key_is_the_ordered_pair(self):
        x, y = uuid4(), uuid4()
        forward = VectorRecord(entity_id=y, tenant_id=x, vector=[1.0])
        reversed_ = VectorRecord(entity_id=x, tenant_id=y, vector=[2.0])

        assert deduplicate([forward, reversed_]) == [forward, reversed_]

    def test_one_entity_id_under_two_tenants_survives_as_two_rows(self):
        entity_id = uuid4()
        one = VectorRecord(entity_id=entity_id, tenant_id=uuid4(), vector=[1.0])
        two = VectorRecord(entity_id=entity_id, tenant_id=uuid4(), vector=[2.0])

        assert deduplicate([one, two]) == [one, two]

    def test_order_is_otherwise_preserved(self):
        records = [
            VectorRecord(entity_id=uuid4(), tenant_id=uuid4(), vector=[float(n)]) for n in range(4)
        ]
        assert deduplicate(records) == records

    def test_an_empty_batch_stays_empty(self):
        assert deduplicate([]) == []
