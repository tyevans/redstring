"""The vector domain types: the score scale, and what metadata may hold."""

from __future__ import annotations

import math
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from redstring.domain.vector import VectorMatch, VectorRecord, cosine_score, is_zero_vector

_components = st.floats(
    min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False, allow_subnormal=False
)
# Bounded on the norm rather than on "some component is non-zero": see
# `test_a_vector_whose_norm_underflows_is_treated_as_zero`.
_vectors = st.lists(_components, min_size=4, max_size=4).filter(
    lambda values: sum(value * value for value in values) > 1e-12
)


class TestCosineScore:
    def test_an_identical_vector_scores_one(self):
        assert cosine_score([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_an_opposite_vector_scores_zero(self):
        assert cosine_score([1.0, 2.0], [-1.0, -2.0]) == pytest.approx(0.0, abs=1e-12)

    def test_an_orthogonal_vector_scores_a_half(self):
        assert cosine_score([1.0, 0.0], [0.0, 1.0]) == 0.5

    def test_magnitude_does_not_affect_the_score(self):
        """Cosine is about direction; a rescaled vector is the same direction."""
        assert cosine_score([1.0, 0.0], [17.0, 0.0]) == pytest.approx(1.0)

    @given(left=_vectors, right=_vectors)
    def test_the_score_is_always_within_the_models_bound(
        self, left: list[float], right: list[float]
    ) -> None:
        """`VectorMatch.score` is bounded `0..1`, so this must never exceed it.

        Slice 0 shipped a `cosine_similarity` that returned marginally more
        than 1.0 for identical float32 vectors through accumulated rounding,
        which broke exactly such a bound. Hence the clamp, and hence this.
        """
        score = cosine_score(left, right)
        assert 0.0 <= score <= 1.0
        VectorMatch(entity_id=uuid4(), score=score)

    @given(vector=_vectors)
    def test_every_vector_scores_one_against_itself(self, vector: list[float]) -> None:
        """The boundary the clamp exists for, over generated values.

        Note the tolerance: the clamp guarantees `<= 1.0`, **not** exactly
        1.0. Rounding moves the self-similarity of a float vector in either
        direction -- `[0.0, 1.0, 1.5, 1.25]` scores 0.9999999999999999 -- and
        clamping can only pull the overshoot back. That is why every score
        assertion in the compliance suite compares with a tolerance, and it is
        the honest contract rather than a weakened one.
        """
        score = cosine_score(vector, vector)
        assert score <= 1.0
        assert score == pytest.approx(1.0)

    @given(left=_vectors, right=_vectors)
    def test_the_score_is_symmetric(self, left: list[float], right: list[float]) -> None:
        assert cosine_score(left, right) == pytest.approx(cosine_score(right, left))

    @given(left=_vectors, right=_vectors)
    def test_the_score_is_the_documented_transform_of_cosine(
        self, left: list[float], right: list[float]
    ) -> None:
        """`(1 + cosine) / 2`, not raw cosine and not a distance.

        Ranking is invariant under any monotone transform, so no ordering test
        can tell those apart -- and `min_score` is a number callers carry
        between adapters, so the scale itself has to be pinned.
        """
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
        assert cosine_score(left, right) == pytest.approx((1 + dot / norms) / 2, abs=1e-12)

    def test_a_zero_vector_has_no_score(self):
        with pytest.raises(ValueError, match="zero"):
            cosine_score([0.0, 0.0], [1.0, 1.0])

    def test_a_vector_whose_norm_underflows_is_treated_as_zero(self):
        """Non-zero components, zero magnitude.

        Every component here is a perfectly good float and `is_zero_vector`
        says the vector is fine, but each squares to zero, so the norm is zero
        and cosine is genuinely undefined. The port's guard asks about the
        components; this asks about the norm, and the two disagree in exactly
        this band. Recorded as BACKLOG B10l rather than fixed, because closing
        it means deciding what an embedding of magnitude 1e-160 should mean
        rather than merely adding a check.
        """
        tiny = [1e-200, 1e-200]
        assert not is_zero_vector(tiny)
        with pytest.raises(ValueError, match="zero"):
            cosine_score(tiny, tiny)

    def test_vectors_of_different_lengths_are_a_programming_error(self):
        """`strict=True` on the zip: silently truncating to the shorter one
        would produce a plausible score for two incomparable vectors."""
        with pytest.raises(ValueError, match="argument 2 is shorter"):
            cosine_score([1.0, 2.0], [1.0])


class TestIsZeroVector:
    @pytest.mark.parametrize(
        ("vector", "expected"),
        [
            ([0.0, 0.0], True),
            ([], True),
            ([-0.0, 0.0], True),  # negative zero is still zero
            ([0.0, 1e-30], False),
            ([0.0, -1.0], False),
        ],
    )
    def test_recognises_the_origin(self, vector: list[float], expected: bool):
        assert is_zero_vector(vector) is expected


class TestMetadataMustBeStorableAsJson:
    """Postgres `jsonb` cannot hold a NUL in text; a Python dict can.

    Rejecting it on the domain type rather than in either adapter is what
    keeps the two from diverging: without this, the in-memory store accepts
    metadata pgvector refuses, and the shared compliance suite passes on data
    that cannot be persisted.
    """

    @pytest.mark.parametrize(
        "metadata",
        [
            pytest.param({"\x00": "k"}, id="in-a-key"),
            pytest.param({"k": "a\x00b"}, id="in-a-value"),
            pytest.param({"k": {"nested": "a\x00"}}, id="nested-dict"),
            pytest.param({"k": ["fine", "a\x00"]}, id="nested-list"),
            pytest.param({"k": [{"deeper": "\x00"}]}, id="list-of-dicts"),
        ],
    )
    def test_a_nul_anywhere_is_rejected(self, metadata: dict[str, object]):
        with pytest.raises(ValueError, match="NUL"):
            VectorRecord(entity_id=uuid4(), tenant_id=uuid4(), vector=[1.0], metadata=metadata)
        with pytest.raises(ValueError, match="NUL"):
            VectorMatch(entity_id=uuid4(), score=1.0, metadata=metadata)

    @pytest.mark.parametrize(
        "metadata",
        [
            {},
            {"entity_type": "person"},
            {"unicode": "é中\U0001f600"},
            {"nested": {"deep": [1, None, True, {"x": []}]}},
            # Other control characters are fine: jsonb escapes them and only
            # NUL is unrepresentable in Postgres text.
            {"tab": "a\tb", "newline": "a\nb", "bell": "\x07"},
        ],
    )
    def test_ordinary_metadata_is_accepted(self, metadata: dict[str, object]):
        record = VectorRecord(entity_id=uuid4(), tenant_id=uuid4(), vector=[1.0], metadata=metadata)
        assert record.metadata == metadata


class TestVectorMatchBounds:
    @pytest.mark.parametrize("score", [-0.001, 1.001, 2.0])
    def test_a_score_outside_zero_to_one_is_rejected(self, score: float):
        """The bound is the tripwire for a distance/similarity inversion: an
        adapter reporting a raw cosine would hand `-1.0` straight to this."""
        with pytest.raises(ValueError, match="score"):
            VectorMatch(entity_id=uuid4(), score=score)

    @pytest.mark.parametrize("score", [0.0, 0.5, 1.0])
    def test_the_endpoints_are_valid(self, score: float):
        assert VectorMatch(entity_id=uuid4(), score=score).score == score
