"""Tests for redstring.events.streams -- how a stream id is derived."""

from uuid import UUID, uuid4

import pytest

from redstring.events.streams import (
    CONSOLIDATION_CATEGORY,
    DOCUMENT_CATEGORY,
    consolidation_stream,
    document_stream,
)


def test_a_document_stream_is_in_the_document_category():
    stream = document_stream(tenant_id=uuid4(), source_id="doc-1")
    assert stream.category == DOCUMENT_CATEGORY
    assert isinstance(stream.aggregate_id, UUID)


def test_a_consolidation_stream_is_the_tenant_in_the_consolidation_category():
    tenant_id = uuid4()
    stream = consolidation_stream(tenant_id=tenant_id)
    assert stream.aggregate_id == tenant_id
    assert stream.category == CONSOLIDATION_CATEGORY


def test_the_same_document_derives_the_same_id_every_time():
    tenant_id, source_id = uuid4(), "doc-1"
    assert document_stream(tenant_id=tenant_id, source_id=source_id) == document_stream(
        tenant_id=tenant_id, source_id=source_id
    )


def test_two_documents_of_one_tenant_get_different_streams():
    tenant_id = uuid4()
    assert document_stream(tenant_id=tenant_id, source_id="a") != document_stream(
        tenant_id=tenant_id, source_id="b"
    )


def test_one_source_id_under_two_tenants_gets_different_streams():
    """The tenant is half the key, and a derivation that ignored it would pass
    every test that only varies the source id.

    `SourceId` is a caller-supplied string -- two tenants ingesting the same
    public URL is the *expected* case, not a corner one. A derivation keyed on
    the source id alone would silently give them one shared stream, which is a
    cross-tenant write on the only path this project treats as inviolable.
    """
    source_id = "https://example.org/ada"
    assert document_stream(tenant_id=uuid4(), source_id=source_id) != document_stream(
        tenant_id=uuid4(), source_id=source_id
    )


def test_the_two_halves_of_the_key_cannot_be_confused_for_each_other():
    """Where one document's key ends and the next begins must be unambiguous.

    A derivation that concatenated the two halves before hashing would map
    ("t", "ab") and ("ta", "b") onto one stream. The tenant is a fixed-width
    UUID here rather than text, which is what makes the split unambiguous --
    this test is what fails if a later change makes either half free-form.
    """
    tenant_a = UUID("00000000-0000-0000-0000-0000000000ab")
    tenant_b = UUID("00000000-0000-0000-0000-000000000000")
    assert document_stream(tenant_id=tenant_a, source_id="c") != document_stream(
        tenant_id=tenant_b, source_id="abc"
    )


@pytest.mark.parametrize("source_id", ["", "   "])
def test_a_blank_source_id_is_rejected(source_id):
    """`SourceDocument.id` is a free-form string with no validation of its own,
    so the stream derivation is the last place a blank one can be caught.
    Hashed rather than rejected, it would produce a perfectly valid-looking
    stream that every blank-id document shares.
    """
    with pytest.raises(ValueError, match="source_id"):
        document_stream(tenant_id=uuid4(), source_id=source_id)
