"""
Unit tests for vector operations service.

Tests the vector_ops module functions for embedding similarity calculations.
"""

import pytest

from kg_builder.services.vector_ops import compute_embedding_similarity


@pytest.mark.unit
def test_compute_embedding_similarity_identical():
    """Identical embeddings should have similarity of 1.0."""
    embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
    similarity = compute_embedding_similarity(embedding, embedding)
    assert abs(similarity - 1.0) < 1e-6


@pytest.mark.unit
def test_compute_embedding_similarity_orthogonal():
    """Orthogonal embeddings should have similarity of 0.0."""
    embedding1 = [1.0, 0.0, 0.0]
    embedding2 = [0.0, 1.0, 0.0]
    similarity = compute_embedding_similarity(embedding1, embedding2)
    assert abs(similarity) < 1e-6


@pytest.mark.unit
def test_compute_embedding_similarity_opposite():
    """Opposite embeddings should have similarity of -1.0."""
    embedding1 = [1.0, 0.0, 0.0]
    embedding2 = [-1.0, 0.0, 0.0]
    similarity = compute_embedding_similarity(embedding1, embedding2)
    assert abs(similarity - (-1.0)) < 1e-6


@pytest.mark.unit
def test_compute_embedding_similarity_similar():
    """Similar embeddings should have high similarity."""
    embedding1 = [0.9, 0.1, 0.0]
    embedding2 = [0.85, 0.15, 0.0]
    similarity = compute_embedding_similarity(embedding1, embedding2)
    assert similarity > 0.95


@pytest.mark.unit
def test_compute_embedding_similarity_different():
    """Different embeddings should have lower similarity."""
    embedding1 = [1.0, 0.0, 0.0]
    embedding2 = [0.0, 0.0, 1.0]
    similarity = compute_embedding_similarity(embedding1, embedding2)
    assert similarity < 0.1


@pytest.mark.unit
def test_compute_embedding_similarity_mismatched_dimensions():
    """Mismatched dimensions should raise ValueError."""
    embedding1 = [0.1, 0.2, 0.3]
    embedding2 = [0.1, 0.2]
    with pytest.raises(ValueError, match="same dimension"):
        compute_embedding_similarity(embedding1, embedding2)


@pytest.mark.unit
def test_compute_embedding_similarity_zero_vector():
    """Zero vector should return 0.0 similarity."""
    embedding1 = [0.0, 0.0, 0.0]
    embedding2 = [0.1, 0.2, 0.3]
    similarity = compute_embedding_similarity(embedding1, embedding2)
    assert similarity == 0.0


@pytest.mark.unit
def test_compute_embedding_similarity_high_dimension():
    """Test with high dimensional embeddings (like bge-m3)."""
    import random
    random.seed(42)

    # Generate two random 1024-dimensional embeddings
    embedding1 = [random.random() for _ in range(1024)]
    embedding2 = [random.random() for _ in range(1024)]

    similarity = compute_embedding_similarity(embedding1, embedding2)

    # Random vectors in high dimensions tend to be orthogonal
    # but should still produce a valid similarity score
    assert -1.0 <= similarity <= 1.0


@pytest.mark.unit
def test_compute_embedding_similarity_normalized():
    """Normalized embeddings should produce expected results."""
    import math

    # Create normalized unit vectors
    embedding1 = [1.0 / math.sqrt(3), 1.0 / math.sqrt(3), 1.0 / math.sqrt(3)]
    embedding2 = [1.0 / math.sqrt(3), 1.0 / math.sqrt(3), 1.0 / math.sqrt(3)]

    similarity = compute_embedding_similarity(embedding1, embedding2)
    assert abs(similarity - 1.0) < 1e-6


@pytest.mark.unit
def test_compute_embedding_similarity_real_world():
    """Test with realistic embedding values."""
    # Simulate embeddings for "Python programming" and "Python language"
    # These would be similar in a real embedding space
    embedding1 = [0.25, 0.35, 0.15, 0.10, 0.05, 0.02, 0.03, 0.05]
    embedding2 = [0.22, 0.38, 0.12, 0.08, 0.08, 0.04, 0.02, 0.06]

    similarity = compute_embedding_similarity(embedding1, embedding2)
    assert similarity > 0.9  # Should be very similar
