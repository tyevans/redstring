"""Non-destructive consolidation dry-run preview and tenant-wide consolidation sweep."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from redstring.composition.build_graph import ConsolidationReport, Consolidator
from redstring.composition.themes.models import DEFAULT_PAGE_SIZE, MAX_PAGES
from redstring.consolidation.candidates import CandidateFinder
from redstring.consolidation.policy import (
    HIGH_SIMILARITY,
    LOW_SIMILARITY,
    MergeDecision,
    decide,
)
from redstring.domain.entity import Entity
from redstring.domain.normalization import normalize_name
from redstring.temporal.query import CursorStalledError

if TYPE_CHECKING:
    from redstring.consolidation.protocols import (
        CandidateSource,
        ConsolidationGraph,
        MergeAdjudicator,
    )
    from redstring.domain.ids import EntityId, TenantId
    from redstring.domain.limiter import CallLimiter
    from redstring.domain.merge_strategy import PropertyMergePolicy
    from redstring.domain.similarity import FeatureWeights, SimilarityFeatures
    from redstring.ports.graph_store import GraphStore
    from redstring.ports.vector_store import VectorReader, VectorStore

__all__ = [
    "ConsolidationPreview",
    "PreviewCandidate",
    "TenantConsolidationReport",
    "consolidate_tenant",
    "preview_consolidation",
]


@dataclass(frozen=True)
class PreviewCandidate:
    """One candidate duplicate pair evaluated during a dry-run consolidation preview."""

    subject: Entity
    candidate: Entity
    features: SimilarityFeatures
    score: float
    decision: MergeDecision


@dataclass(frozen=True)
class ConsolidationPreview:
    """Dry-run consolidation preview across a tenant's canonical entities."""

    tenant_id: TenantId
    entities_scanned: int
    merge_candidates: tuple[PreviewCandidate, ...]
    adjudicate_candidates: tuple[PreviewCandidate, ...]
    reject_candidates: tuple[PreviewCandidate, ...]
    rejected_count: int
    elapsed_ms: float


@dataclass(frozen=True)
class TenantConsolidationReport:
    """Summary of a live tenant-wide consolidation sweep."""

    tenant_id: TenantId
    entities_scanned: int
    aliases_skipped: int
    merges_executed: int
    entities_absorbed: int
    relationships_redirected: int
    reports: tuple[ConsolidationReport, ...]
    elapsed_ms: float


async def _list_canonical_entities(
    tenant_id: TenantId,
    graph_store: ConsolidationGraph,
    page_size: int,
) -> tuple[list[Entity], int]:
    entities: list[Entity] = []
    cursor: EntityId | None = None
    finished = False
    for _ in range(MAX_PAGES):
        page = await graph_store.find_entities(tenant_id, limit=page_size, after=cursor)
        entities.extend(page)
        if len(page) < page_size:
            finished = True
            break
        cursor = page[-1].id
    if not finished:
        raise CursorStalledError(tenant_id, MAX_PAGES)
    if not entities:
        return [], 0
    resolved = await graph_store.resolve_entity_ids([e.id for e in entities], tenant_id)
    canonicals = [e for e in entities if resolved.get(e.id, e.id) == e.id]
    aliases_skipped = len(entities) - len(canonicals)
    return canonicals, aliases_skipped


async def _resolve_subject_filter(
    subject: Entity | EntityId | str,
    tenant_id: TenantId,
    graph_store: ConsolidationGraph,
) -> list[Entity]:
    if isinstance(subject, Entity):
        return [subject]
    if isinstance(subject, str):
        matches = await graph_store.find_entities(tenant_id, name=normalize_name(subject), limit=1)
        return list(matches)
    found = await graph_store.get_entity(subject, tenant_id)
    return [found] if found is not None else []


async def preview_consolidation(
    tenant_id: TenantId,
    *,
    graph_store: ConsolidationGraph,
    subject: Entity | EntityId | str | None = None,
    vector_store: VectorReader | None = None,
    finder: CandidateSource | None = None,
    weights: FeatureWeights | None = None,
    use_graph_signal: bool = True,
    high: float = HIGH_SIMILARITY,
    low: float = LOW_SIMILARITY,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> ConsolidationPreview:
    """Evaluate blocked candidate pairs in ``tenant_id`` without mutating any store."""
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")

    started = time.perf_counter()
    if subject is not None:
        canonicals = await _resolve_subject_filter(subject, tenant_id, graph_store)
    else:
        canonicals, _ = await _list_canonical_entities(tenant_id, graph_store, page_size)

    active_finder = (
        finder
        if finder is not None
        else CandidateFinder(
            graph_store,
            vector_store=vector_store,
            weights=weights,
            use_graph_signal=use_graph_signal,
        )
    )

    seen_pairs: set[tuple[str, str]] = set()
    merge_list: list[PreviewCandidate] = []
    adjudicate_list: list[PreviewCandidate] = []
    reject_list: list[PreviewCandidate] = []

    for subj in canonicals:
        scored = await active_finder.candidates(subj, minimum_score=0.0)
        for cand in scored:
            s_str = str(subj.id)
            c_str = str(cand.entity.id)
            pair_key = (s_str, c_str) if s_str <= c_str else (c_str, s_str)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            decision = decide(cand.score, high=high, low=low)
            item = PreviewCandidate(
                subject=subj,
                candidate=cand.entity,
                features=cand.features,
                score=cand.score,
                decision=decision,
            )
            if decision is MergeDecision.MERGE:
                merge_list.append(item)
            elif decision is MergeDecision.ADJUDICATE:
                adjudicate_list.append(item)
            else:
                reject_list.append(item)

    sort_key = lambda p: (-p.score, str(p.subject.id), str(p.candidate.id))  # noqa: E731
    merge_list.sort(key=sort_key)
    adjudicate_list.sort(key=sort_key)
    reject_list.sort(key=sort_key)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return ConsolidationPreview(
        tenant_id=tenant_id,
        entities_scanned=len(canonicals),
        merge_candidates=tuple(merge_list),
        adjudicate_candidates=tuple(adjudicate_list),
        reject_candidates=tuple(reject_list),
        rejected_count=len(reject_list),
        elapsed_ms=elapsed_ms,
    )


async def consolidate_tenant(
    tenant_id: TenantId,
    *,
    store: GraphStore,
    consolidator: Consolidator | None = None,
    vector_store: VectorStore | None = None,
    finder: CandidateSource | None = None,
    adjudicator: MergeAdjudicator | None = None,
    weights: FeatureWeights | None = None,
    use_graph_signal: bool = True,
    merge_policy: PropertyMergePolicy | None = None,
    concurrency: int = 1,
    limiter: CallLimiter | None = None,
    high: float = HIGH_SIMILARITY,
    low: float = LOW_SIMILARITY,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> TenantConsolidationReport:
    """Sweep all canonical entities in ``tenant_id`` and execute confirmed merges."""
    if page_size < 1:
        raise ValueError(f"page_size must be >= 1, got {page_size}")

    started = time.perf_counter()
    canonicals, aliases_skipped = await _list_canonical_entities(tenant_id, store, page_size)
    active_consolidator = (
        consolidator
        if consolidator is not None
        else Consolidator(
            store,
            vector_store=vector_store,
            weights=weights,
            use_graph_signal=use_graph_signal,
            merge_policy=merge_policy,
        )
    )
    reports = await active_consolidator.resolve_many(
        canonicals,
        finder=finder,
        adjudicator=adjudicator,
        concurrency=concurrency,
        limiter=limiter,
        high=high,
        low=low,
    )
    entities_absorbed = sum(len(r.affected_entity_ids) for r in reports)
    relationships_redirected = sum(r.relationships_changed for r in reports)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return TenantConsolidationReport(
        tenant_id=tenant_id,
        entities_scanned=len(canonicals),
        aliases_skipped=aliases_skipped,
        merges_executed=len(reports),
        entities_absorbed=entities_absorbed,
        relationships_redirected=relationships_redirected,
        reports=tuple(reports),
        elapsed_ms=elapsed_ms,
    )
