"""The retrieval result types."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from redstring.domain.entity import Entity
from redstring.domain.provenance import ExtractionMethod
from redstring.domain.retrieval import RetrievalMode, RetrievalResult, ScoredEntity


def _entity(**overrides):
    fields = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "name": "Ada Lovelace",
        "normalized_name": "ada lovelace",
        "entity_type": "person",
        "extraction_method": ExtractionMethod.LLM,
        "confidence": 0.9,
    }
    fields.update(overrides)
    return Entity(**fields)


def test_a_channel_that_did_not_rank_is_none_not_zero() -> None:
    """`None` means "not ranked here"; `0.0` means "ranked, and scored zero".

    They are different facts and a caller acts on them differently -- one says
    the lexical channel was off, the other says the name did not match. A type
    that collapsed them would make `semantic is None` unaskable.
    """
    unranked = ScoredEntity(entity=_entity(), score=0.5, semantic=None, lexical=0.9)
    ranked_zero = ScoredEntity(entity=_entity(), score=0.5, semantic=0.0, lexical=0.9)
    assert unranked.semantic is None
    assert ranked_zero.semantic == 0.0
    assert unranked.semantic != ranked_zero.semantic


def test_component_scores_default_to_none() -> None:
    """Constructed directly, not through a factory -- the defaults are public.

    Every test building this type through a helper that passes every field
    leaves the declared defaults unexecuted while the signature invites direct
    construction.
    """
    scored = ScoredEntity(entity=_entity(), score=0.5)
    assert scored.semantic is None
    assert scored.lexical is None


@pytest.mark.parametrize("bad", [-0.01, 1.01])
def test_a_component_score_outside_zero_to_one_is_rejected(bad: float) -> None:
    """Both components are on stated 0..1 scales, so the bound is enforceable.

    `score` is not: RRF is ordinal and unbounded, which is the whole reason it
    carries no `le`/`ge`.
    """
    with pytest.raises(ValidationError):
        ScoredEntity(entity=_entity(), score=0.5, semantic=bad)


def test_the_fused_score_is_not_bounded_to_one() -> None:
    """Two channels at rank 0 sum to 2/60, but nothing in the type caps it.

    Pinning a 0..1 bound here would be the `VectorMatch` scale leaking onto a
    number that is not on it.
    """
    assert ScoredEntity(entity=_entity(), score=7.5).score == 7.5


def test_a_result_keeps_the_query_it_answered() -> None:
    result = RetrievalResult(query="ada", matches=[])
    assert result.query == "ada"
    assert result.matches == []


def test_the_modes_are_their_own_strings() -> None:
    assert RetrievalMode.HYBRID == "hybrid"
    assert RetrievalMode.SEMANTIC == "semantic"
    assert RetrievalMode.LEXICAL == "lexical"
