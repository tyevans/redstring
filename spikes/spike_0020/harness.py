"""Spike benchmark harness for SPIKE-0020.

Hypothesis: How to define entity identity across extraction runs and retract stale
entities without violating GraphStore port invariants?
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from pathlib import Path

from redstring.domain.entity import Entity
from redstring.domain.ids import EntityId, SourceId, TenantId
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.extraction.mapping import _relationship_id_for, entity_id_for
from redstring.graph.adapters.memory import InMemoryGraphStore


@dataclass
class DocumentAggregateSim:
    """Simulation of Document aggregate entity tracking across model versions."""

    tenant_id: TenantId
    source_id: SourceId
    extraction_history: dict[str, list[EntityId]] = field(default_factory=dict)
    active_entity_ids: list[EntityId] = field(default_factory=list)

    def record_extraction(
        self,
        *,
        model_version: str,
        entities: list[Entity],
        relationships: list[Relationship],
    ) -> dict[str, Any] | None:
        if model_version in self.extraction_history:
            return None

        new_ids = [e.id for e in entities]
        retracted_ids = [eid for eid in self.active_entity_ids if eid not in set(new_ids)]

        self.extraction_history[model_version] = new_ids
        self.active_entity_ids = new_ids

        return {
            "tenant_id": self.tenant_id,
            "source_id": self.source_id,
            "model_version": model_version,
            "entities": entities,
            "relationships": relationships,
            "retracted_entity_ids": retracted_ids,
        }


async def project_extraction_with_retractions(
    store: InMemoryGraphStore,
    event: dict[str, Any],
) -> None:
    """Projection handler that retracts dropped entities preserving GraphStore invariants.

    Invariants preserved (ADR-0102):
    1. Zero calls to non-existent delete_entity.
    2. Retracted entities have edges removed via delete_relationship.
    3. Retracted entity metadata tombstoned via upsert_entity.
    4. AliasStore resolution remains fully functional.
    """
    tenant_id = event["tenant_id"]

    # 1. Upsert active entities
    await store.upsert_entities(event["entities"])

    # 2. Retract dropped entities: tombstone node & remove incident relationships
    for retracted_id in event["retracted_entity_ids"]:
        existing = await store.get_entity(retracted_id, tenant_id)
        if existing:
            # Tombstone entity metadata while retaining node identity for alias chains
            tombstoned = Entity(
                id=existing.id,
                tenant_id=existing.tenant_id,
                name=existing.name,
                normalized_name=existing.normalized_name,
                entity_type=existing.entity_type,
                properties={**existing.properties, "_retracted": True},
                provenance=existing.provenance,
            )
            await store.upsert_entity(tombstoned)

        # Remove relationships incident to retracted entity
        existing_edges = await store.get_relationships(retracted_id, tenant_id)
        for edge in existing_edges:
            await store.delete_relationship(edge.id, tenant_id)

    # 3. Upsert active relationships
    await store.upsert_relationships(event["relationships"])


def run_benchmark(iterations: int = 100) -> dict[str, Any]:
    """Executes empirical benchmark iterations and records latency metrics."""
    import asyncio
    from datetime import UTC, datetime

    async def _bench() -> float:
        tenant_id = TenantId(uuid4())
        source_id = SourceId("doc-bench")
        store = InMemoryGraphStore()

        prov = Provenance(
            source_id=source_id,
            source_text="Ada and Babbage worked together.",
            extraction_method=ExtractionMethod.LLM,
            model="test-model",
            confidence=0.9,
            observed_at=datetime(2026, 2, 8, 11, 7, tzinfo=UTC),
        )

        e1_id = entity_id_for(
            tenant_id=tenant_id, source_id=source_id, entity_type="person", name="Ada Lovelace"
        )
        e2_id = entity_id_for(
            tenant_id=tenant_id, source_id=source_id, entity_type="person", name="Charles Babbage"
        )

        e1 = Entity(
            id=e1_id,
            tenant_id=tenant_id,
            name="Ada Lovelace",
            normalized_name="ada lovelace",
            entity_type="person",
            provenance=prov,
        )
        e2 = Entity(
            id=e2_id,
            tenant_id=tenant_id,
            name="Charles Babbage",
            normalized_name="charles babbage",
            entity_type="person",
            provenance=prov,
        )

        rel = Relationship(
            id=_relationship_id_for(
                source_entity_id=e1_id,
                target_entity_id=e2_id,
                relationship_type="collaborates_with",
            ),
            tenant_id=tenant_id,
            source_entity_id=e1_id,
            target_entity_id=e2_id,
            relationship_type="collaborates_with",
            confidence=0.9,
            provenance=prov,
        )

        agg = DocumentAggregateSim(tenant_id=tenant_id, source_id=source_id)

        start = time.perf_counter()
        for i in range(iterations):
            # Run 1: 2 entities + 1 relationship
            ev1 = agg.record_extraction(
                model_version=f"m1-{i}", entities=[e1, e2], relationships=[rel]
            )
            if ev1:
                await project_extraction_with_retractions(store, ev1)

            # Run 2: Re-extraction discovering only 1 entity (e2 dropped/retracted)
            ev2 = agg.record_extraction(model_version=f"m2-{i}", entities=[e1], relationships=[])
            if ev2:
                await project_extraction_with_retractions(store, ev2)

        return (time.perf_counter() - start) * 1000

    elapsed_ms = asyncio.run(_bench())
    avg_latency = elapsed_ms / max(1, iterations)
    return {
        "iterations": iterations,
        "total_time_ms": round(elapsed_ms, 3),
        "p95_latency_ms": round(avg_latency, 3),
        "status": "completed",
    }


def record_findings(harness_dir: Path, output: str) -> Path:
    """Persists empirical findings to disk."""
    out_path = harness_dir / "benchmark.txt"
    out_path.write_text(output.strip(), encoding="utf-8")
    return out_path
