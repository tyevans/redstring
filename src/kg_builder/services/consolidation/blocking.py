"""
Blocking engine for entity consolidation candidate generation.

Blocking reduces the comparison space from O(n^2) to O(n) by only
comparing entities within the same "block". Multiple blocking keys
ensure good recall while keeping computation tractable.

This module implements Stage 1 of the entity consolidation pipeline:
identifying candidate pairs that are worth comparing using similarity
metrics.

Blocking Strategies:
- PREFIX: Entities with same first N characters of normalized_name
- ENTITY_TYPE: Entities with same entity_type
- SOUNDEX: Entities with same soundex phonetic code
- TRIGRAM: Entities with high trigram similarity (using pg_trgm)

Each strategy leverages database indexes created in P1-005 for
efficient O(log n) lookups instead of full table scans.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

import jellyfish
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from kg_builder.models.extracted_entity import ExtractedEntity

if TYPE_CHECKING:
    from kg_builder.models.consolidation_config import ConsolidationConfig

logger = logging.getLogger(__name__)


class BlockingStrategy(str, Enum):
    """Available blocking strategies for candidate generation."""

    PREFIX = "prefix"  # Normalized name prefix match
    ENTITY_TYPE = "entity_type"  # Same entity type
    SOUNDEX = "soundex"  # Phonetic similarity using Soundex
    TRIGRAM = "trigram"  # Trigram similarity using pg_trgm
    COMBINED = "combined"  # All strategies OR'd together


@dataclass
class BlockingResult:
    """
    Result from a blocking operation.

    Contains candidate entities and metadata about the blocking process.

    Attributes:
        candidates: List of candidate entities for similarity comparison
        strategies_used: Which blocking strategies were applied
        block_sizes: Number of candidates from each strategy
        total_candidates: Total unique candidates found
        truncated: Whether max_block_size limit was hit
        execution_time_ms: Time taken for the blocking operation
    """

    candidates: list[ExtractedEntity]
    strategies_used: list[BlockingStrategy]
    block_sizes: dict[str, int] = field(default_factory=dict)
    total_candidates: int = 0
    truncated: bool = False
    execution_time_ms: float = 0.0


class BlockingEngine:
    """
    Engine for generating merge candidates using blocking.

    Blocking reduces the search space by only comparing entities that
    share a blocking key. Multiple blocking strategies are combined
    using OR semantics to maximize recall while maintaining efficiency.

    Strategies:
    - PREFIX: Entities with same first N chars of normalized_name
    - ENTITY_TYPE: Entities with same entity_type
    - SOUNDEX: Entities with same soundex code

    The engine ensures:
    - Only canonical entities are returned (not aliases)
    - Tenant isolation is enforced
    - Block sizes are limited to prevent memory issues
    - Which blocking keys matched is tracked for each candidate

    Example:
        engine = BlockingEngine(max_block_size=500)
        result = await engine.find_candidates(session, entity, tenant_id)
        for candidate in result.candidates:
            blocking_keys = candidate._blocking_keys
            # Compare entity with candidate
    """

    def __init__(
        self,
        max_block_size: int = 500,
        min_prefix_length: int = 5,
        strategies: list[BlockingStrategy] | None = None,
    ):
        """
        Initialize the blocking engine.

        Args:
            max_block_size: Maximum number of candidates to return per entity.
                           Larger values increase recall but use more memory.
            min_prefix_length: Minimum characters for prefix blocking. Names
                              shorter than this won't use prefix strategy.
            strategies: Which blocking strategies to use. Defaults to
                       [PREFIX, ENTITY_TYPE, SOUNDEX] if not specified.
        """
        self.max_block_size = max_block_size
        self.min_prefix_length = min_prefix_length
        self.strategies = strategies or [
            BlockingStrategy.PREFIX,
            BlockingStrategy.ENTITY_TYPE,
            BlockingStrategy.SOUNDEX,
        ]
        logger.debug(
            f"BlockingEngine initialized: max_block_size={max_block_size}, "
            f"min_prefix_length={min_prefix_length}, strategies={self.strategies}"
        )

    async def find_candidates(
        self,
        session: AsyncSession,
        entity: ExtractedEntity,
        tenant_id: UUID,
        config: ConsolidationConfig | None = None,
    ) -> BlockingResult:
        """
        Find merge candidates for an entity using blocking.

        Applies configured blocking strategies and returns entities that
        match any of the blocking keys. Only returns canonical entities
        from the same tenant.

        Args:
            session: Async database session
            entity: Entity to find candidates for
            tenant_id: Tenant ID for isolation
            config: Optional tenant config (for max_block_size override)

        Returns:
            BlockingResult with candidates and metadata

        Note:
            Each returned candidate will have a `_blocking_keys` attribute
            containing the list of strategies that matched it.
        """
        start_time = time.perf_counter()
        max_size = config.max_block_size if config else self.max_block_size

        # Build blocking conditions for each strategy
        blocking_conditions = []
        strategies_used: list[BlockingStrategy] = []
        block_sizes: dict[str, int] = {}

        for strategy in self.strategies:
            condition = self._build_condition(entity, strategy)
            if condition is not None:
                blocking_conditions.append(condition)
                strategies_used.append(strategy)
                logger.debug(f"Added blocking condition for strategy: {strategy.value}")

        if not blocking_conditions:
            logger.warning(
                f"No blocking conditions could be built for entity {entity.id} "
                f"(name='{entity.name}')"
            )
            execution_time = (time.perf_counter() - start_time) * 1000
            return BlockingResult(
                candidates=[],
                strategies_used=[],
                block_sizes={},
                total_candidates=0,
                truncated=False,
                execution_time_ms=execution_time,
            )

        # Build and execute combined query with OR of all blocking conditions
        query = (
            select(ExtractedEntity)
            .where(ExtractedEntity.tenant_id == tenant_id)
            .where(ExtractedEntity.is_canonical == True)  # noqa: E712
            .where(ExtractedEntity.id != entity.id)
            .where(or_(*blocking_conditions))
            .limit(max_size + 1)  # +1 to detect truncation
        )

        result = await session.execute(query)
        candidates = list(result.scalars().all())

        # Check if truncated
        truncated = len(candidates) > max_size
        if truncated:
            candidates = candidates[:max_size]
            logger.info(
                f"Block truncated for entity {entity.id}: "
                f"{len(candidates)} candidates (max {max_size})"
            )

        # Track which blocking key matched each candidate
        for candidate in candidates:
            matched_keys = self._get_matching_keys(entity, candidate)
            # Store as private attribute for downstream processing
            object.__setattr__(candidate, "_blocking_keys", matched_keys)

            # Update block sizes
            for key in matched_keys:
                block_sizes[key] = block_sizes.get(key, 0) + 1

        execution_time = (time.perf_counter() - start_time) * 1000
        logger.debug(
            f"find_candidates for entity {entity.id}: "
            f"{len(candidates)} candidates in {execution_time:.2f}ms"
        )

        return BlockingResult(
            candidates=candidates,
            strategies_used=strategies_used,
            block_sizes=block_sizes,
            total_candidates=len(candidates),
            truncated=truncated,
            execution_time_ms=execution_time,
        )

    def find_candidates_sync(
        self,
        session: Session,
        entity: ExtractedEntity,
        tenant_id: UUID,
        config: ConsolidationConfig | None = None,
    ) -> BlockingResult:
        """
        Find merge candidates for an entity using blocking (sync version).

        Synchronous version of find_candidates for use in Celery tasks
        that use sync database sessions.

        Args:
            session: Sync database session
            entity: Entity to find candidates for
            tenant_id: Tenant ID for isolation
            config: Optional tenant config (for max_block_size override)

        Returns:
            BlockingResult with candidates and metadata
        """
        start_time = time.perf_counter()
        max_size = config.max_block_size if config else self.max_block_size

        # Build blocking conditions for each strategy
        blocking_conditions = []
        strategies_used: list[BlockingStrategy] = []
        block_sizes: dict[str, int] = {}

        for strategy in self.strategies:
            condition = self._build_condition(entity, strategy)
            if condition is not None:
                blocking_conditions.append(condition)
                strategies_used.append(strategy)

        if not blocking_conditions:
            execution_time = (time.perf_counter() - start_time) * 1000
            return BlockingResult(
                candidates=[],
                strategies_used=[],
                block_sizes={},
                total_candidates=0,
                truncated=False,
                execution_time_ms=execution_time,
            )

        # Build and execute combined query with OR of all blocking conditions
        query = (
            select(ExtractedEntity)
            .where(ExtractedEntity.tenant_id == tenant_id)
            .where(ExtractedEntity.is_canonical == True)  # noqa: E712
            .where(ExtractedEntity.id != entity.id)
            .where(or_(*blocking_conditions))
            .limit(max_size + 1)
        )

        result = session.execute(query)
        candidates = list(result.scalars().all())

        # Check if truncated
        truncated = len(candidates) > max_size
        if truncated:
            candidates = candidates[:max_size]

        # Track which blocking key matched each candidate
        for candidate in candidates:
            matched_keys = self._get_matching_keys(entity, candidate)
            object.__setattr__(candidate, "_blocking_keys", matched_keys)
            for key in matched_keys:
                block_sizes[key] = block_sizes.get(key, 0) + 1

        execution_time = (time.perf_counter() - start_time) * 1000
        return BlockingResult(
            candidates=candidates,
            strategies_used=strategies_used,
            block_sizes=block_sizes,
            total_candidates=len(candidates),
            truncated=truncated,
            execution_time_ms=execution_time,
        )

    async def find_candidates_batch(
        self,
        session: AsyncSession,
        entities: list[ExtractedEntity],
        tenant_id: UUID,
        config: ConsolidationConfig | None = None,
    ) -> dict[UUID, BlockingResult]:
        """
        Find candidates for multiple entities (batch operation).

        More efficient than calling find_candidates multiple times
        when processing many entities, though currently executes
        sequentially. Future optimization could batch database queries.

        Args:
            session: Async database session
            entities: List of entities to find candidates for
            tenant_id: Tenant ID for isolation
            config: Optional tenant config

        Returns:
            Dictionary mapping entity ID to BlockingResult
        """
        results: dict[UUID, BlockingResult] = {}
        for entity in entities:
            results[entity.id] = await self.find_candidates(session, entity, tenant_id, config)
        return results

    def _build_condition(
        self,
        entity: ExtractedEntity,
        strategy: BlockingStrategy,
    ):
        """
        Build SQLAlchemy condition for a blocking strategy.

        Args:
            entity: Source entity to build condition for
            strategy: Blocking strategy to apply

        Returns:
            SQLAlchemy condition or None if strategy not applicable
        """
        if strategy == BlockingStrategy.PREFIX:
            # Prefix blocking on normalized name
            if not entity.normalized_name:
                return None
            prefix = entity.normalized_name[: self.min_prefix_length]
            if len(prefix) < self.min_prefix_length:
                logger.debug(f"Name too short for prefix blocking: '{entity.normalized_name}'")
                return None
            return ExtractedEntity.normalized_name.startswith(prefix)

        elif strategy == BlockingStrategy.ENTITY_TYPE:
            # Same entity type
            return ExtractedEntity.entity_type == entity.entity_type

        elif strategy == BlockingStrategy.SOUNDEX:
            # Same soundex code - uses name_soundex generated column
            # The name_soundex column is a generated column from LM-003
            soundex_code = self.compute_soundex(entity.name)
            if not soundex_code:
                return None
            # Use text() for the generated column reference
            return text("name_soundex = :soundex").bindparams(soundex=soundex_code)

        elif strategy == BlockingStrategy.TRIGRAM:
            # Trigram similarity - requires pg_trgm extension
            # This uses the GIN index created in P1-005
            # Note: threshold is typically set via pg_trgm.similarity_threshold
            # For now, we use a WHERE condition with similarity operator
            if not entity.normalized_name:
                return None
            # Use pg_trgm % operator for similarity
            return text("normalized_name % :name").bindparams(name=entity.normalized_name)

        return None

    def _get_matching_keys(
        self,
        entity: ExtractedEntity,
        candidate: ExtractedEntity,
    ) -> list[str]:
        """
        Determine which blocking keys matched for a candidate.

        This is useful for analysis and debugging to understand
        why a candidate was included in the block.

        Args:
            entity: Source entity
            candidate: Candidate entity

        Returns:
            List of blocking key names that matched
        """
        matched: list[str] = []

        # Check prefix match
        prefix_len = self.min_prefix_length
        entity_prefix = (entity.normalized_name or "")[:prefix_len]
        candidate_prefix = (candidate.normalized_name or "")[:prefix_len]
        if entity_prefix and entity_prefix == candidate_prefix:
            matched.append("prefix")

        # Check type match
        if entity.entity_type == candidate.entity_type:
            matched.append("entity_type")

        # Check soundex match
        entity_soundex = self.compute_soundex(entity.name)
        candidate_soundex = self.compute_soundex(candidate.name)
        if entity_soundex and entity_soundex == candidate_soundex:
            matched.append("soundex")

        return matched

    @staticmethod
    def compute_soundex(name: str) -> str:
        """
        Compute soundex code for a name.

        Soundex is a phonetic algorithm that indexes names by sound
        as pronounced in English. It was designed for surname encoding.

        Args:
            name: Name to encode

        Returns:
            Soundex code (e.g., "R163" for "Robert")

        Note:
            Soundex has limitations:
            - Designed for English names
            - Ignores vowels after first letter
            - May over-match for technical terms
        """
        if not name:
            return ""
        return jellyfish.soundex(name)

    @staticmethod
    def compute_metaphone(name: str) -> str:
        """
        Compute Metaphone phonetic code for a name.

        Metaphone is an improved phonetic algorithm that handles
        more phonetic rules than Soundex.

        Args:
            name: Name to encode

        Returns:
            Metaphone code
        """
        if not name:
            return ""
        return jellyfish.metaphone(name)

    @staticmethod
    def compute_nysiis(name: str) -> str:
        """
        Compute NYSIIS phonetic code for a name.

        NYSIIS (New York State Identification and Intelligence System)
        is a phonetic algorithm that handles some American name
        pronunciations better than Soundex.

        Args:
            name: Name to encode

        Returns:
            NYSIIS code
        """
        if not name:
            return ""
        return jellyfish.nysiis(name)

    @staticmethod
    def compute_prefix(normalized_name: str, length: int = 5) -> str:
        """
        Get prefix of normalized name for blocking.

        Args:
            normalized_name: Normalized entity name
            length: Prefix length

        Returns:
            Prefix string
        """
        if not normalized_name:
            return ""
        return normalized_name[:length]

    async def get_block_statistics(
        self,
        session: AsyncSession,
        tenant_id: UUID,
    ) -> dict:
        """
        Get statistics about blocking keys for a tenant.

        Useful for monitoring and tuning blocking strategies.

        Args:
            session: Async database session
            tenant_id: Tenant ID

        Returns:
            Dictionary with blocking statistics
        """
        # Count entities by type
        type_query = (
            select(
                ExtractedEntity.entity_type,
                func.count(ExtractedEntity.id).label("count"),
            )
            .where(ExtractedEntity.tenant_id == tenant_id)
            .where(ExtractedEntity.is_canonical == True)  # noqa: E712
            .group_by(ExtractedEntity.entity_type)
        )
        type_result = await session.execute(type_query)
        type_counts = {row[0].value: row[1] for row in type_result}

        # Count distinct soundex codes
        soundex_query = (
            select(func.count(func.distinct(text("name_soundex"))))
            .select_from(ExtractedEntity)
            .where(ExtractedEntity.tenant_id == tenant_id)
            .where(ExtractedEntity.is_canonical == True)  # noqa: E712
        )
        soundex_result = await session.execute(soundex_query)
        distinct_soundex = soundex_result.scalar() or 0

        # Total canonical entities
        total_query = (
            select(func.count(ExtractedEntity.id))
            .where(ExtractedEntity.tenant_id == tenant_id)
            .where(ExtractedEntity.is_canonical == True)  # noqa: E712
        )
        total_result = await session.execute(total_query)
        total_canonical = total_result.scalar() or 0

        return {
            "total_canonical_entities": total_canonical,
            "entities_by_type": type_counts,
            "distinct_soundex_codes": distinct_soundex,
            "strategies_configured": [s.value for s in self.strategies],
            "max_block_size": self.max_block_size,
            "min_prefix_length": self.min_prefix_length,
        }
