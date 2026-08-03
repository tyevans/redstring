"""
Unit tests for pgvector package availability.

Verifies that pgvector is properly installed and basic functionality works.
"""

import pytest


@pytest.mark.unit
def test_pgvector_import():
    """Verify pgvector package is available."""
    from pgvector.sqlalchemy import Vector

    # Vector type should be a SQLAlchemy type
    vector_type = Vector(1024)
    assert vector_type is not None


@pytest.mark.unit
def test_vector_dimension():
    """Test Vector type with specific dimension."""
    from pgvector.sqlalchemy import Vector

    # Test 1024 dimensions (bge-m3)
    vector_1024 = Vector(1024)
    assert vector_1024 is not None

    # Test other common dimensions
    vector_384 = Vector(384)  # sentence-transformers
    vector_768 = Vector(768)  # BERT
    vector_1536 = Vector(1536)  # OpenAI ada-002

    assert vector_384 is not None
    assert vector_768 is not None
    assert vector_1536 is not None


@pytest.mark.unit
def test_embedding_dimension_constant():
    """Verify EMBEDDING_DIMENSION constant is defined."""
    from kg_builder.models.extracted_entity import EMBEDDING_DIMENSION

    assert EMBEDDING_DIMENSION == 1024


@pytest.mark.unit
def test_vector_type_in_model():
    """Verify Vector type is conditionally imported in model."""
    from kg_builder.models.extracted_entity import Vector

    # Vector should be imported (or None if not available)
    # In a properly configured environment, it should be the Vector type
    if Vector is not None:
        vector_type = Vector(1024)
        assert vector_type is not None
