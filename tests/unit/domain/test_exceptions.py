"""Tests for kg_builder.domain.exceptions."""

from uuid import uuid4

import pytest

from kg_builder.domain.exceptions import KgBuilderError, MissingEntityError


def test_missing_entity_error_is_a_domain_error():
    assert issubclass(MissingEntityError, KgBuilderError)


def test_domain_error_is_an_exception():
    assert issubclass(KgBuilderError, Exception)


def test_missing_entity_error_carries_the_ids_it_could_not_find():
    entity_id, tenant_id = uuid4(), uuid4()
    error = MissingEntityError(entity_id=entity_id, tenant_id=tenant_id)
    assert error.entity_id == entity_id
    assert error.tenant_id == tenant_id


def test_missing_entity_error_message_names_both_ids():
    entity_id, tenant_id = uuid4(), uuid4()
    message = str(MissingEntityError(entity_id=entity_id, tenant_id=tenant_id))
    assert str(entity_id) in message
    assert str(tenant_id) in message


def test_missing_entity_error_is_raisable():
    with pytest.raises(MissingEntityError):
        raise MissingEntityError(entity_id=uuid4(), tenant_id=uuid4())
