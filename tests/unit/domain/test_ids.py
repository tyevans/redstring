"""Tests for redstring.domain.ids.

The four names are `NewType`s, not bare aliases. What that buys is *nominal*
typing at the type checker and nothing at runtime, so the tests here are in
two halves: what a caller may rely on at runtime (an `EntityId` **is** a
`UUID`, and every existing call site keeps working), and what only a type
checker can see (a `TenantId` is not an `EntityId`).

The second half cannot be asserted with `assert`. `EntityId is not TenantId`
would pass just as happily for two unrelated `NewType`s over `int`, and it
would also have passed for the previous bare aliases had they been spelled
`EntityId = UUID` and `TenantId = uuid.UUID`... which they were not, and which
is exactly why the old tests here (`assert EntityId is uuid.UUID`) could not
distinguish four names from one. So the nominal half is asserted through
`__supertype__` and the *distinctness* of the four objects, and the real proof
that mypy rejects a swap is the configured `mypy --strict` run over `src/`,
which is a gate rather than a test in this file.
"""

import uuid

from redstring.domain.ids import EntityId, RelationshipId, SourceId, TenantId

ALL_IDS = (EntityId, RelationshipId, TenantId, SourceId)


def test_the_three_uuid_ids_wrap_uuid():
    assert EntityId.__supertype__ is uuid.UUID
    assert RelationshipId.__supertype__ is uuid.UUID
    assert TenantId.__supertype__ is uuid.UUID


def test_source_id_wraps_str():
    assert SourceId.__supertype__ is str


def test_the_four_names_are_four_distinct_types():
    """The whole point. Bare aliases collapsed to two objects, not four."""
    assert len({id(name) for name in ALL_IDS}) == 4


def test_construction_is_the_identity_at_runtime():
    """`NewType` costs nothing at runtime, which is what keeps callers working.

    Asserted with `is`, not `==`: a wrapper class would satisfy `==` through
    `__eq__` while changing every `isinstance` check and every dict key in the
    library. Identity is the claim.
    """
    raw = uuid.uuid4()
    assert EntityId(raw) is raw
    assert TenantId(raw) is raw
    assert RelationshipId(raw) is raw

    text = "doc-1"
    assert SourceId(text) is text


def test_a_wrapped_id_is_still_an_instance_of_its_supertype():
    """Domain models annotate against these, and pydantic validates the base."""
    assert isinstance(EntityId(uuid.uuid4()), uuid.UUID)
    assert isinstance(SourceId("doc-1"), str)


def test_an_unwrapped_value_is_accepted_by_a_domain_model():
    """The runtime contract callers already had: a bare `UUID` still works.

    This is what makes the change source-compatible. Every existing call site
    passing `uuid4()` where an `EntityId` is annotated keeps working; only a
    type checker is newly opinionated about it.
    """
    from redstring.domain.entity import Entity
    from redstring.domain.provenance import ExtractionMethod

    entity = Entity(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="Ada",
        normalized_name="ada",
        entity_type="person",
        extraction_method=ExtractionMethod.LLM,
        confidence=0.9,
    )
    assert isinstance(entity.id, uuid.UUID)
