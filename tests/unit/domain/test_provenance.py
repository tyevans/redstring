"""`Provenance` holds the invariants that were on `Entity` for want of a home."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from pydantic import ValidationError

from redstring.domain.provenance import ExtractionMethod, Provenance

OBSERVED = datetime(2026, 8, 12, 9, 30, tzinfo=UTC)


def test_a_provenance_records_when_and_how_the_claim_was_made() -> None:
    provenance = Provenance(
        observed_at=OBSERVED,
        extraction_method=ExtractionMethod.LLM,
        confidence=0.8,
        model="ollama/qwen3.6-27b-mtp",
    )
    assert provenance.observed_at == OBSERVED
    assert provenance.model == "ollama/qwen3.6-27b-mtp"


def test_a_naive_observed_at_is_refused() -> None:
    """A naive datetime raises `TypeError` only at the moment of comparison,
    which for `MOST_RECENTLY_OBSERVED` is deep inside a merge. Refuse it here.
    """
    with pytest.raises(ValidationError, match="timezone-aware"):
        Provenance(
            observed_at=datetime(2026, 8, 12, 9, 30),
            extraction_method=ExtractionMethod.PATTERN,
            confidence=0.5,
        )


#: Values just outside the bound, pinned rather than left to the sampler.
#:
#: These four moved here with the field, from `test_entity.py`. They are not
#: decoration: `st.floats().filter(...)` reaches the far extremes readily and
#: the immediate neighbourhood of `1.0` rarely, so a mutant widening the bound
#: to `<= 2.0` survived the property test entirely. `.claude/rules/testing.md`
#: cites that survivor by name.
#:
#: `1.0 + 1e-9` is the one that earns its place. A coarse pair like
#: `[-0.1, 1.1]` kills a grossly widened bound and lets `<= 1.05` through --
#: which is the mutation an off-by-a-little edit actually produces.
JUST_OUTSIDE_CONFIDENCE = [-1e-9, 1.0 + 1e-9, 1.5, 2.0]


@given(st.floats(allow_nan=False, allow_infinity=False).filter(lambda f: f < 0.0 or f > 1.0))
@example(confidence=-1e-9)
@example(confidence=1.0 + 1e-9)
@example(confidence=1.5)
@example(confidence=2.0)
def test_confidence_outside_the_unit_interval_is_refused(confidence: float) -> None:
    with pytest.raises(ValidationError, match="confidence"):
        Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=confidence,
        )


@given(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
def test_confidence_inside_the_unit_interval_is_accepted(confidence: float) -> None:
    """The other half. Without it, a validator rejecting *everything* passes
    the test above and nothing else here would notice -- the bounds test uses
    two values, and two values are not a range."""
    assert (
        Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=confidence,
        ).confidence
        == confidence
    )


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_the_bounds_of_the_unit_interval_are_allowed(confidence: float) -> None:
    """Pinned as examples rather than left to a range check nobody exercises:
    `0.0 <= x <= 1.0` mutated to `<` at either end passes every interior value.
    """
    assert (
        Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=confidence,
        ).confidence
        == confidence
    )


@pytest.mark.parametrize(
    "method",
    [
        ExtractionMethod.PATTERN,
        ExtractionMethod.SCHEMA_ORG,
        ExtractionMethod.OPEN_GRAPH,
        ExtractionMethod.MANUAL,
    ],
)
def test_a_method_that_invokes_no_model_may_not_name_one(
    method: ExtractionMethod,
) -> None:
    with pytest.raises(ValidationError, match="invokes no model"):
        Provenance(
            observed_at=OBSERVED,
            extraction_method=method,
            confidence=0.5,
            model="ollama/qwen3.6-27b-mtp",
        )


@pytest.mark.parametrize("method", [ExtractionMethod.LLM, ExtractionMethod.HYBRID])
def test_a_model_bearing_method_may_name_one(method: ExtractionMethod) -> None:
    """`HYBRID` is the case worth pinning: pattern-matching *plus* a model is
    precisely where knowing which model contributed matters.
    """
    assert (
        Provenance(
            observed_at=OBSERVED,
            extraction_method=method,
            confidence=0.5,
            model="anthropic/claude-opus-4-20250514",
        ).model
        is not None
    )


def test_unstorable_text_in_a_free_form_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=0.5,
            source_text="before\x00after",
        )


def test_an_aware_observed_at_in_a_non_utc_offset_is_kept_as_given() -> None:
    """The offset is part of the value, not decoration. A validator that
    normalised to UTC would agree with this test only if it also compared
    equal, so the assertion is on `utcoffset()` rather than on the instant.
    """
    from datetime import timedelta, timezone

    offset = timezone(timedelta(hours=-5))
    provenance = Provenance(
        observed_at=datetime(2026, 3, 1, 14, 45, 30, tzinfo=offset),
        extraction_method=ExtractionMethod.PATTERN,
        confidence=0.5,
    )
    assert provenance.observed_at.utcoffset() == timedelta(hours=-5)
