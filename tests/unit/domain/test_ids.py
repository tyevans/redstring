"""Tests for redstring.domain.ids."""

import uuid

from redstring.domain.ids import EntityId, RelationshipId, SourceId, TenantId


def test_entity_id_is_uuid():
    assert EntityId is uuid.UUID


def test_relationship_id_is_uuid():
    assert RelationshipId is uuid.UUID


def test_tenant_id_is_uuid():
    assert TenantId is uuid.UUID


def test_source_id_is_str():
    assert SourceId is str
