"""
LLM-assisted entity merger for cross-chunk entity resolution.

Uses LLM to resolve ambiguous entity references that simple
string matching cannot handle reliably.
"""

import logging
from collections import defaultdict

import httpx
from jellyfish import jaro_winkler_similarity
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from kg_builder.config import settings
from kg_builder.preprocessing.factory import EntityMergerFactory, EntityMergerType
from kg_builder.preprocessing.mergers.simple_merger import SimpleMerger
from kg_builder.preprocessing.schemas import EntityMergeCandidate, EntityMergeDecision

logger = logging.getLogger(__name__)


class MergeDecisionItem(BaseModel):
    """Single merge decision from LLM."""

    pair_index: int = Field(description="Index of the candidate pair (0-based)")
    should_merge: bool = Field(description="Whether these entities should be merged")
    merged_name: str | None = Field(default=None, description="Canonical name to use if merging")
    confidence: float = Field(default=0.8, description="Confidence in the decision (0.0-1.0)")
    reasoning: str | None = Field(default=None, description="Brief explanation for the decision")


class LLMMergeResponse(BaseModel):
    """Structured response from LLM for merge decisions."""

    decisions: list[MergeDecisionItem] = Field(
        description="List of merge decisions for each candidate pair"
    )


@EntityMergerFactory.register(EntityMergerType.LLM)
class LLMMerger:
    """Entity merger that uses LLM for ambiguous resolution.

    Process:
    1. First pass: Simple deduplication by exact/high-similarity name match
    2. Second pass: Find ambiguous candidates (similarity between thresholds)
    3. Third pass: LLM resolution for ambiguous candidates

    The LLM is only called for cases where:
    - Names are similar but not identical (0.7 < similarity < 0.9)
    - Context suggests possible match but isn't certain
    - Type mismatch but names are very similar

    This approach minimizes LLM calls while maximizing merge accuracy.

    Attributes:
        high_threshold: Threshold for automatic merge without LLM (default: 0.9)
        low_threshold: Minimum similarity to consider as candidate (default: 0.7)
        use_llm_for_ambiguous: Whether to use LLM for ambiguous cases
        batch_size: Number of candidates per LLM call

    Example:
        merger = LLMMerger(high_threshold=0.9, low_threshold=0.7)
        merged_entities, merged_rels = await merger.merge_entities(
            entities_by_chunk={0: [...], 1: [...]},
            relationships_by_chunk={0: [...], 1: [...]},
            document_context="This document is about...",
        )
    """

    SYSTEM_PROMPT = """You are an entity resolution expert analyzing entities extracted from a document.
Your task is to determine whether pairs of entity references from different parts of a document refer to the same real-world entity.

For each candidate pair, analyze:
1. Name similarity (exact match, abbreviation, nickname, alternate spelling, etc.)
2. Entity type compatibility (same type, or compatible types like "Person" and "Author")
3. Context clues from descriptions and source text
4. Domain knowledge about common variations

Guidelines:
- BE CONSERVATIVE: Only merge when you're confident they're the same entity
- False positives (wrong merges) are worse than false negatives (missed merges)
- Consider that the same name might refer to different entities (e.g., "John" in different contexts)
- Consider that different names might refer to the same entity (e.g., "JFK" and "John F. Kennedy")

For each pair, provide:
- should_merge: true ONLY if they definitely refer to the same entity
- merged_name: the best canonical name to use (prefer more complete names)
- confidence: your confidence level (0.0-1.0)
- reasoning: brief explanation of your decision"""

    def __init__(
        self,
        high_threshold: float = 0.90,
        low_threshold: float = 0.70,
        use_llm_for_ambiguous: bool = True,
        batch_size: int = 10,
        ollama_base_url: str | None = None,
        ollama_model: str | None = None,
        timeout: int = 120,
    ):
        """Initialize LLM entity merger.

        Args:
            high_threshold: Threshold for automatic merge without LLM (default: 0.9)
            low_threshold: Minimum similarity to consider for LLM resolution (default: 0.7)
            use_llm_for_ambiguous: Whether to use LLM for ambiguous cases (default: True)
            batch_size: Number of candidates per LLM call (default: 10)
            ollama_base_url: Ollama server URL (defaults to settings)
            ollama_model: Model name (defaults to settings)
            timeout: Request timeout in seconds (default: 120)
        """
        self._high_threshold = high_threshold
        self._low_threshold = low_threshold
        self._use_llm = use_llm_for_ambiguous
        self._batch_size = batch_size
        self._timeout = timeout

        # For high-confidence automatic merges
        self._simple_merger = SimpleMerger(
            similarity_threshold=high_threshold,
            require_type_match=True,
        )

        # Initialize LLM if enabled
        if self._use_llm:
            base_url = ollama_base_url or settings.OLLAMA_BASE_URL
            model = ollama_model or settings.OLLAMA_MODEL

            if base_url:
                http_client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=30.0))

                self._model = OpenAIModel(
                    model_name=model,
                    base_url=f"{base_url}/v1",
                    api_key="ollama",
                    http_client=http_client,
                )

                self._agent: Agent[None, LLMMergeResponse] = Agent(
                    model=self._model,
                    result_type=LLMMergeResponse,
                    system_prompt=self.SYSTEM_PROMPT,
                    result_retries=2,
                )
            else:
                logger.warning("OLLAMA_BASE_URL not configured, LLM merging disabled")
                self._use_llm = False
                self._model = None
                self._agent = None
        else:
            self._model = None
            self._agent = None

        logger.info(
            "LLMMerger initialized",
            extra={
                "high_threshold": high_threshold,
                "low_threshold": low_threshold,
                "use_llm": self._use_llm,
                "batch_size": batch_size,
            },
        )

    @property
    def merger_type(self) -> str:
        """Return the type identifier for this merger."""
        return EntityMergerType.LLM.value

    async def merge_entities(
        self,
        entities_by_chunk: dict[int, list[dict]],
        relationships_by_chunk: dict[int, list[dict]],
        document_context: str | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Merge entities and relationships from multiple chunks.

        Three-phase approach:
        1. Simple merge for exact/high-similarity matches
        2. Find ambiguous candidates
        3. LLM resolution for ambiguous candidates

        Args:
            entities_by_chunk: Map of chunk_index -> list of entity dicts
            relationships_by_chunk: Map of chunk_index -> list of relationship dicts
            document_context: Optional document context for LLM

        Returns:
            Tuple of (merged_entities, merged_relationships)
        """
        # Phase 1: Simple merge for high-confidence matches
        merged_entities, merged_rels = await self._simple_merger.merge_entities(
            entities_by_chunk, relationships_by_chunk, document_context
        )

        initial_count = len(merged_entities)

        # Phase 2: Find ambiguous candidates for LLM resolution
        if self._use_llm and self._agent:
            candidates = self._find_ambiguous_candidates(merged_entities)

            if candidates:
                logger.info(
                    "Found ambiguous candidates for LLM resolution",
                    extra={"candidate_count": len(candidates)},
                )

                # Phase 3: Resolve with LLM
                decisions = await self.resolve_candidates(candidates)

                # Apply LLM decisions
                merged_entities = self._apply_llm_decisions(merged_entities, candidates, decisions)

                # Re-merge relationships with updated entities
                merged_rels = self._simple_merger._merge_relationships(
                    relationships_by_chunk, merged_entities
                )

        logger.info(
            "LLM merge complete",
            extra={
                "entities_before": initial_count,
                "entities_after": len(merged_entities),
                "relationships": len(merged_rels),
            },
        )

        return merged_entities, merged_rels

    async def resolve_candidates(
        self,
        candidates: list[EntityMergeCandidate],
    ) -> list[EntityMergeDecision]:
        """Use LLM to resolve ambiguous candidates.

        Args:
            candidates: List of candidate pairs

        Returns:
            List of merge decisions
        """
        if not candidates:
            return []

        if not self._use_llm or not self._agent:
            # Fall back to simple threshold-based decision
            return await self._simple_merger.resolve_candidates(candidates)

        decisions: list[EntityMergeDecision] = []

        # Process in batches
        for batch_start in range(0, len(candidates), self._batch_size):
            batch = candidates[batch_start : batch_start + self._batch_size]
            batch_decisions = await self._resolve_batch(batch)
            decisions.extend(batch_decisions)

        return decisions

    async def _resolve_batch(
        self,
        batch: list[EntityMergeCandidate],
    ) -> list[EntityMergeDecision]:
        """Resolve a batch of candidates with LLM.

        Args:
            batch: Batch of candidates

        Returns:
            List of decisions for this batch
        """
        # Build prompt
        prompt = self._build_resolution_prompt(batch)

        try:
            result = await self._agent.run(prompt)

            # Map decisions back to candidates
            decisions_by_idx = {d.pair_index: d for d in result.data.decisions}

            decisions = []
            for idx, candidate in enumerate(batch):
                if idx in decisions_by_idx:
                    llm_decision = decisions_by_idx[idx]
                    decisions.append(
                        EntityMergeDecision(
                            should_merge=llm_decision.should_merge,
                            merged_name=llm_decision.merged_name,
                            confidence=llm_decision.confidence,
                            reasoning=llm_decision.reasoning,
                        )
                    )
                else:
                    # LLM didn't provide decision for this index - be conservative
                    decisions.append(
                        EntityMergeDecision(
                            should_merge=False,
                            confidence=0.0,
                            reasoning="no_llm_decision",
                        )
                    )

            return decisions

        except Exception as e:
            logger.error(
                "LLM resolution failed",
                extra={"error": str(e), "batch_size": len(batch)},
            )

            # Return conservative decisions (don't merge)
            return [
                EntityMergeDecision(
                    should_merge=False,
                    confidence=0.0,
                    reasoning=f"llm_error: {e!s}",
                )
                for _ in batch
            ]

    def _build_resolution_prompt(self, candidates: list[EntityMergeCandidate]) -> str:
        """Build LLM prompt for candidate resolution.

        Args:
            candidates: List of candidates

        Returns:
            Formatted prompt string
        """
        prompt_parts = [
            "Analyze the following entity pairs and determine if they should be merged:\n"
        ]

        for idx, candidate in enumerate(candidates):
            prompt_parts.append(f"""
Pair {idx}:
  Entity A:
    Name: "{candidate.entity_a_name}"
    Type: {candidate.entity_a_type}
    Description: {candidate.entity_a_description or "N/A"}
    Context: {(candidate.entity_a_context or "N/A")[:200]}
  Entity B:
    Name: "{candidate.entity_b_name}"
    Type: {candidate.entity_b_type}
    Description: {candidate.entity_b_description or "N/A"}
    Context: {(candidate.entity_b_context or "N/A")[:200]}
  String similarity: {candidate.similarity_score:.2f}
""")

        prompt_parts.append(
            "\nProvide your decisions for each pair. Be conservative - only merge when confident."
        )

        return "\n".join(prompt_parts)

    def _find_ambiguous_candidates(
        self,
        entities: list[dict],
    ) -> list[EntityMergeCandidate]:
        """Find entity pairs that are ambiguous (need LLM resolution).

        Ambiguous = similarity between low_threshold and high_threshold

        Args:
            entities: List of entities after simple merge

        Returns:
            List of ambiguous candidate pairs
        """
        candidates = []
        seen_pairs: set[tuple] = set()

        for i, entity_a in enumerate(entities):
            for j, entity_b in enumerate(entities[i + 1 :], i + 1):
                name_a = entity_a.get("name", "").lower()
                name_b = entity_b.get("name", "").lower()

                # Skip if we've seen this pair
                pair_key = tuple(sorted([name_a, name_b]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                # Calculate similarity
                similarity = jaro_winkler_similarity(name_a, name_b)

                # Check if it's in the ambiguous range
                if self._low_threshold <= similarity < self._high_threshold:
                    candidates.append(
                        EntityMergeCandidate(
                            entity_a_name=entity_a.get("name", ""),
                            entity_a_type=entity_a.get("type", ""),
                            entity_a_chunk_index=entity_a.get("_chunk_index", 0),
                            entity_a_context=entity_a.get("source_text"),
                            entity_a_description=entity_a.get("description"),
                            entity_b_name=entity_b.get("name", ""),
                            entity_b_type=entity_b.get("type", ""),
                            entity_b_chunk_index=entity_b.get("_chunk_index", 0),
                            entity_b_context=entity_b.get("source_text"),
                            entity_b_description=entity_b.get("description"),
                            similarity_score=similarity,
                        )
                    )

        return candidates

    def _apply_llm_decisions(
        self,
        entities: list[dict],
        candidates: list[EntityMergeCandidate],
        decisions: list[EntityMergeDecision],
    ) -> list[dict]:
        """Apply LLM merge decisions to entity list.

        Args:
            entities: Current entity list
            candidates: Candidate pairs that were evaluated
            decisions: LLM decisions for each candidate

        Returns:
            Updated entity list after applying merges
        """
        # Build merge graph: which entities should be merged together
        merge_groups: dict[str, set[str]] = defaultdict(set)

        for candidate, decision in zip(candidates, decisions):
            if decision.should_merge:
                name_a = candidate.entity_a_name.lower()
                name_b = candidate.entity_b_name.lower()
                canonical = (decision.merged_name or candidate.entity_a_name).lower()

                merge_groups[canonical].add(name_a)
                merge_groups[canonical].add(name_b)

        # If no merges, return as-is
        if not merge_groups:
            return entities

        # Build name-to-canonical mapping
        name_to_canonical: dict[str, str] = {}
        for canonical, names in merge_groups.items():
            for name in names:
                name_to_canonical[name] = canonical

        # Apply merges
        merged_entities: list[dict] = []
        processed_names: set[str] = set()

        for entity in entities:
            name = entity.get("name", "").lower()

            if name in processed_names:
                continue

            canonical = name_to_canonical.get(name)
            if canonical and canonical in merge_groups:
                # Find all entities in this merge group
                group_names = merge_groups[canonical]
                group_entities = [e for e in entities if e.get("name", "").lower() in group_names]

                # Merge the group
                merged = self._merge_entity_group(group_entities, canonical)
                merged_entities.append(merged)

                # Mark all names in group as processed
                processed_names.update(group_names)
            else:
                merged_entities.append(entity)
                processed_names.add(name)

        return merged_entities

    def _merge_entity_group(
        self,
        group: list[dict],
        canonical_name: str,
    ) -> dict:
        """Merge a group of entities.

        Args:
            group: List of entities to merge
            canonical_name: Canonical name to use

        Returns:
            Merged entity dict
        """
        if not group:
            return {}

        # Sort by confidence (descending)
        sorted_group = sorted(group, key=lambda e: e.get("confidence", 0.0), reverse=True)

        # Start with highest confidence entity
        merged = sorted_group[0].copy()

        # Use canonical name (capitalize properly)
        # Find the version with best capitalization
        best_name = canonical_name
        for entity in group:
            name = entity.get("name", "")
            if name.lower() == canonical_name and len(name) >= len(best_name):
                best_name = name

        merged["name"] = best_name

        # Merge properties
        merged_props = merged.get("properties", {}).copy()
        for entity in sorted_group[1:]:
            entity_props = entity.get("properties", {})
            for key, value in entity_props.items():
                if key not in merged_props:
                    merged_props[key] = value

        merged["properties"] = merged_props

        # Merge descriptions (take longest)
        descriptions = [e.get("description", "") for e in group if e.get("description")]
        if descriptions:
            merged["description"] = max(descriptions, key=len)

        return merged
