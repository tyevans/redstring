"""
Simple entity merger using exact and fuzzy string matching.

Provides basic entity deduplication without LLM assistance.
Useful for:
- Fast processing when LLM isn't available
- Cases where entities have consistent naming
- Fallback when LLM merging fails
"""

import logging
from collections import defaultdict

from jellyfish import jaro_winkler_similarity

from kg_builder.preprocessing.factory import EntityMergerFactory, EntityMergerType
from kg_builder.preprocessing.schemas import EntityMergeCandidate, EntityMergeDecision

logger = logging.getLogger(__name__)


@EntityMergerFactory.register(EntityMergerType.SIMPLE)
class SimpleMerger:
    """Entity merger using string similarity for deduplication.

    Merging strategy:
    1. Exact match: Entities with identical normalized names are merged
    2. High similarity: Entities with Jaro-Winkler similarity >= threshold
       and matching types are merged automatically
    3. Keep highest confidence: When merging, keeps entity with highest confidence

    This merger does NOT use LLM assistance. For ambiguous cases that
    require context understanding, use LLMMerger instead.

    Attributes:
        similarity_threshold: Jaro-Winkler threshold for automatic merge (0.0-1.0)
        case_sensitive: Whether name comparison is case-sensitive

    Example:
        merger = SimpleMerger(similarity_threshold=0.9)
        merged_entities, merged_rels = await merger.merge_entities(
            entities_by_chunk={0: [...], 1: [...]},
            relationships_by_chunk={0: [...], 1: [...]},
        )
    """

    def __init__(
        self,
        similarity_threshold: float = 0.92,
        case_sensitive: bool = False,
        require_type_match: bool = True,
    ):
        """Initialize simple merger.

        Args:
            similarity_threshold: Jaro-Winkler threshold for merge (default: 0.92)
            case_sensitive: Case-sensitive comparison (default: False)
            require_type_match: Require entity types to match for merge (default: True)
        """
        self._similarity_threshold = similarity_threshold
        self._case_sensitive = case_sensitive
        self._require_type_match = require_type_match

        logger.info(
            "SimpleMerger initialized",
            extra={
                "similarity_threshold": similarity_threshold,
                "case_sensitive": case_sensitive,
                "require_type_match": require_type_match,
            },
        )

    @property
    def merger_type(self) -> str:
        """Return the type identifier for this merger."""
        return EntityMergerType.SIMPLE.value

    async def merge_entities(
        self,
        entities_by_chunk: dict[int, list[dict]],
        relationships_by_chunk: dict[int, list[dict]],
        document_context: str | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Merge entities and relationships from multiple chunks.

        Args:
            entities_by_chunk: Map of chunk_index -> list of entity dicts
            relationships_by_chunk: Map of chunk_index -> list of relationship dicts
            document_context: Not used by SimpleMerger

        Returns:
            Tuple of (merged_entities, merged_relationships)
        """
        # Flatten all entities
        all_entities: list[dict] = []
        for chunk_idx in sorted(entities_by_chunk.keys()):
            for entity in entities_by_chunk[chunk_idx]:
                entity_copy = entity.copy()
                entity_copy["_chunk_index"] = chunk_idx
                all_entities.append(entity_copy)

        initial_count = len(all_entities)

        # Group by normalized name + type
        entity_groups: dict[str, list[dict]] = defaultdict(list)
        for entity in all_entities:
            key = self._get_entity_key(entity)
            entity_groups[key].append(entity)

        # Merge exact matches within each group
        merged_entities: list[dict] = []
        for key, group in entity_groups.items():
            merged = self._merge_group(group)
            merged_entities.append(merged)

        # Now check for fuzzy matches across different groups
        merged_entities = self._fuzzy_merge(merged_entities)

        # Clean up internal keys
        for entity in merged_entities:
            entity.pop("_chunk_index", None)
            entity.pop("_merged_from", None)

        # Merge relationships
        merged_relationships = self._merge_relationships(relationships_by_chunk, merged_entities)

        logger.info(
            "Simple merge complete",
            extra={
                "entities_before": initial_count,
                "entities_after": len(merged_entities),
                "relationships_merged": len(merged_relationships),
            },
        )

        return merged_entities, merged_relationships

    async def resolve_candidates(
        self,
        candidates: list[EntityMergeCandidate],
    ) -> list[EntityMergeDecision]:
        """Resolve candidates using string similarity only.

        SimpleMerger doesn't use LLM - it just applies the similarity threshold.

        Args:
            candidates: List of candidate pairs

        Returns:
            List of merge decisions
        """
        decisions = []

        for candidate in candidates:
            should_merge = candidate.similarity_score >= self._similarity_threshold

            if self._require_type_match:
                should_merge = should_merge and (
                    candidate.entity_a_type.lower() == candidate.entity_b_type.lower()
                )

            decisions.append(
                EntityMergeDecision(
                    should_merge=should_merge,
                    merged_name=candidate.entity_a_name if should_merge else None,
                    merged_type=candidate.entity_a_type if should_merge else None,
                    confidence=candidate.similarity_score,
                    reasoning="string_similarity_threshold",
                )
            )

        return decisions

    def _get_entity_key(self, entity: dict) -> str:
        """Get grouping key for entity.

        Args:
            entity: Entity dict

        Returns:
            Grouping key string
        """
        name = entity.get("name", "")
        entity_type = entity.get("type", "")

        if not self._case_sensitive:
            name = name.lower()
            entity_type = entity_type.lower()

        return f"{name}::{entity_type}"

    def _merge_group(self, group: list[dict]) -> dict:
        """Merge a group of entities with the same key.

        Keeps the entity with highest confidence, merging properties.

        Args:
            group: List of entities with same name+type

        Returns:
            Merged entity dict
        """
        if len(group) == 1:
            return group[0].copy()

        # Sort by confidence (descending)
        sorted_group = sorted(
            group,
            key=lambda e: e.get("confidence", 0.0),
            reverse=True,
        )

        # Start with highest confidence entity
        merged = sorted_group[0].copy()

        # Merge properties from other entities
        merged_props = merged.get("properties", {}).copy()
        for entity in sorted_group[1:]:
            entity_props = entity.get("properties", {})
            for key, value in entity_props.items():
                if key not in merged_props:
                    merged_props[key] = value

        merged["properties"] = merged_props

        # Track which entities were merged
        merged["_merged_from"] = [e.get("name") for e in sorted_group]

        return merged

    def _fuzzy_merge(self, entities: list[dict]) -> list[dict]:
        """Apply fuzzy matching to merge similar entities.

        Args:
            entities: List of pre-grouped entities

        Returns:
            List of entities after fuzzy merging
        """
        if len(entities) <= 1:
            return entities

        # Build list of entities that haven't been merged yet
        result: list[dict] = []
        merged_indices: set[int] = set()

        for i, entity_a in enumerate(entities):
            if i in merged_indices:
                continue

            # Find similar entities
            similar_entities = [entity_a]

            for j, entity_b in enumerate(entities[i + 1 :], i + 1):
                if j in merged_indices:
                    continue

                # Check type match if required
                if self._require_type_match:
                    type_a = entity_a.get("type", "").lower()
                    type_b = entity_b.get("type", "").lower()
                    if type_a != type_b:
                        continue

                # Calculate similarity
                name_a = entity_a.get("name", "")
                name_b = entity_b.get("name", "")

                if not self._case_sensitive:
                    name_a = name_a.lower()
                    name_b = name_b.lower()

                similarity = jaro_winkler_similarity(name_a, name_b)

                if similarity >= self._similarity_threshold:
                    similar_entities.append(entity_b)
                    merged_indices.add(j)

            # Merge similar entities
            merged = self._merge_group(similar_entities)
            result.append(merged)

        return result

    def _merge_relationships(
        self,
        relationships_by_chunk: dict[int, list[dict]],
        merged_entities: list[dict],
    ) -> list[dict]:
        """Merge and deduplicate relationships.

        Updates relationship references to use merged entity names.

        Args:
            relationships_by_chunk: Relationships from each chunk
            merged_entities: Final merged entities

        Returns:
            Deduplicated relationships
        """
        # Build name normalization map
        name_map: dict[str, str] = {}
        for entity in merged_entities:
            canonical_name = entity.get("name", "")
            # Map all merged names to canonical name
            merged_from = entity.get("_merged_from", [canonical_name])
            for name in merged_from:
                name_map[name.lower()] = canonical_name

        # Flatten and normalize relationships
        all_relationships: list[dict] = []
        for chunk_idx in sorted(relationships_by_chunk.keys()):
            for rel in relationships_by_chunk[chunk_idx]:
                rel_copy = rel.copy()

                # Normalize source and target names
                source_name = rel_copy.get("source_name", "")
                target_name = rel_copy.get("target_name", "")

                rel_copy["source_name"] = name_map.get(source_name.lower(), source_name)
                rel_copy["target_name"] = name_map.get(target_name.lower(), target_name)

                all_relationships.append(rel_copy)

        # Deduplicate relationships
        seen: set[tuple] = set()
        unique_rels: list[dict] = []

        for rel in all_relationships:
            key = (
                rel.get("source_name", "").lower(),
                rel.get("target_name", "").lower(),
                rel.get("relationship_type", "").lower(),
            )

            if key not in seen:
                seen.add(key)
                unique_rels.append(rel)

        return unique_rels
