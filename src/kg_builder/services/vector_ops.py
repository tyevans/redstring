"""
Vector operations using pgvector.

Provides efficient similarity queries directly in PostgreSQL using
the pgvector extension for semantic similarity search.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kg_builder.models.extracted_entity import ExtractedEntity


async def find_similar_entities(
    session: AsyncSession,
    embedding: list[float],
    tenant_id: uuid.UUID,
    limit: int = 10,
    threshold: float = 0.7,
    exclude_ids: Sequence[uuid.UUID] | None = None,
) -> list[tuple[ExtractedEntity, float]]:
    """
    Find entities similar to the given embedding vector.

    Uses pgvector's cosine distance operator for efficient similarity search.
    Requires the pgvector extension to be installed and enabled.

    Args:
        session: Database session
        embedding: Query embedding vector (1024 dimensions for bge-m3)
        tenant_id: Tenant ID for RLS filtering
        limit: Maximum results to return (default: 10)
        threshold: Minimum similarity threshold, 0-1 (default: 0.7)
        exclude_ids: Entity IDs to exclude from results

    Returns:
        List of (entity, similarity_score) tuples, ordered by similarity descending

    Example:
        >>> results = await find_similar_entities(
        ...     session=db_session,
        ...     embedding=query_embedding,
        ...     tenant_id=tenant_uuid,
        ...     limit=5,
        ...     threshold=0.8,
        ... )
        >>> for entity, score in results:
        ...     print(f"{entity.name}: {score:.3f}")
    """
    # Cosine distance: 1 - cosine_similarity
    # So we want distance < (1 - threshold)
    max_distance = 1 - threshold

    # Build the query
    query = (
        select(
            ExtractedEntity,
            (1 - ExtractedEntity.embedding.cosine_distance(embedding)).label("similarity"),
        )
        .where(ExtractedEntity.tenant_id == tenant_id)
        .where(ExtractedEntity.embedding.isnot(None))
        .where(ExtractedEntity.embedding.cosine_distance(embedding) < max_distance)
    )

    # Exclude specified IDs if provided
    if exclude_ids:
        query = query.where(ExtractedEntity.id.notin_(exclude_ids))

    # Order by distance (ascending) and limit results
    query = (
        query.order_by(ExtractedEntity.embedding.cosine_distance(embedding))
        .limit(limit)
    )

    result = await session.execute(query)
    return [(row.ExtractedEntity, row.similarity) for row in result]


async def find_similar_to_entity(
    session: AsyncSession,
    entity_id: uuid.UUID,
    tenant_id: uuid.UUID,
    limit: int = 10,
    threshold: float = 0.7,
) -> list[tuple[ExtractedEntity, float]]:
    """
    Find entities similar to a given entity by its ID.

    First fetches the entity's embedding, then performs similarity search.

    Args:
        session: Database session
        entity_id: Source entity ID
        tenant_id: Tenant ID for RLS filtering
        limit: Maximum results to return
        threshold: Minimum similarity threshold

    Returns:
        List of (entity, similarity_score) tuples, excluding the source entity

    Raises:
        ValueError: If entity not found or has no embedding
    """
    # Get the source entity
    result = await session.execute(
        select(ExtractedEntity)
        .where(ExtractedEntity.id == entity_id)
        .where(ExtractedEntity.tenant_id == tenant_id)
    )
    entity = result.scalar_one_or_none()

    if entity is None:
        raise ValueError(f"Entity {entity_id} not found")

    if entity.embedding is None:
        raise ValueError(f"Entity {entity_id} has no embedding")

    # Find similar entities, excluding the source
    return await find_similar_entities(
        session=session,
        embedding=entity.embedding,
        tenant_id=tenant_id,
        limit=limit,
        threshold=threshold,
        exclude_ids=[entity_id],
    )


async def update_entity_embedding(
    session: AsyncSession,
    entity_id: uuid.UUID,
    embedding: list[float],
) -> None:
    """
    Update the embedding for an entity.

    Args:
        session: Database session
        entity_id: Entity ID to update
        embedding: New embedding vector (1024 dimensions for bge-m3)

    Note:
        This does not commit the transaction. The caller should
        handle transaction management.
    """
    await session.execute(
        update(ExtractedEntity)
        .where(ExtractedEntity.id == entity_id)
        .values(embedding=embedding)
    )


async def batch_update_embeddings(
    session: AsyncSession,
    embeddings: dict[uuid.UUID, list[float]],
) -> int:
    """
    Batch update embeddings for multiple entities.

    Args:
        session: Database session
        embeddings: Dictionary mapping entity IDs to embedding vectors

    Returns:
        Number of entities updated
    """
    count = 0
    for entity_id, embedding in embeddings.items():
        await session.execute(
            update(ExtractedEntity)
            .where(ExtractedEntity.id == entity_id)
            .values(embedding=embedding)
        )
        count += 1
    return count


async def get_entities_without_embeddings(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    limit: int = 100,
) -> list[ExtractedEntity]:
    """
    Get entities that don't have embeddings yet.

    Useful for batch embedding generation.

    Args:
        session: Database session
        tenant_id: Tenant ID for RLS filtering
        limit: Maximum entities to return

    Returns:
        List of entities without embeddings
    """
    result = await session.execute(
        select(ExtractedEntity)
        .where(ExtractedEntity.tenant_id == tenant_id)
        .where(ExtractedEntity.embedding.is_(None))
        .limit(limit)
    )
    return list(result.scalars().all())


async def compute_embedding_similarity(
    embedding1: list[float],
    embedding2: list[float],
) -> float:
    """
    Compute cosine similarity between two embeddings.

    This is a pure Python implementation for cases where database
    query is not needed.

    Args:
        embedding1: First embedding vector
        embedding2: Second embedding vector

    Returns:
        Cosine similarity score (0.0 to 1.0)
    """
    if len(embedding1) != len(embedding2):
        raise ValueError("Embeddings must have same dimension")

    # Compute dot product and magnitudes
    dot_product = sum(a * b for a, b in zip(embedding1, embedding2))
    magnitude1 = sum(a * a for a in embedding1) ** 0.5
    magnitude2 = sum(b * b for b in embedding2) ** 0.5

    if magnitude1 == 0 or magnitude2 == 0:
        return 0.0

    return dot_product / (magnitude1 * magnitude2)
