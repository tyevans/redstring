"""The vector domain types: the score scale, and what metadata may hold."""

from __future__ import annotations

import math
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from redstring.domain.vector import VectorMatch, VectorRecord, cosine_score, has_zero_norm

_components = st.floats(
    min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False, allow_subnormal=False
)
# Bounded on the norm rather than on "some component is non-zero": see
# `test_a_vector_whose_norm_underflows_has_no_score`.
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

    def test_a_vector_whose_norm_underflows_has_no_score(self):
        """Non-zero components, zero magnitude.

        Every component here is a perfectly good float64, but each squares to
        zero, so the norm is zero and cosine is genuinely undefined. The guard
        agrees with `cosine_score` about this band -- which is the point of
        `has_zero_norm` asking about the norm rather than the components.
        """
        tiny = [1e-200, 1e-200]
        assert has_zero_norm(tiny)
        with pytest.raises(ValueError, match="zero"):
            cosine_score(tiny, tiny)

    def test_vectors_of_different_lengths_are_a_programming_error(self):
        """`strict=True` on the zip: silently truncating to the shorter one
        would produce a plausible score for two incomparable vectors."""
        with pytest.raises(ValueError, match="argument 2 is shorter"):
            cosine_score([1.0, 2.0], [1.0])


class TestHasZeroNorm:
    @pytest.mark.parametrize(
        ("vector", "expected"),
        [
            ([0.0, 0.0], True),
            ([], True),
            ([-0.0, 0.0], True),  # negative zero is still zero
            ([0.0, -1.0], False),
            ([0.0, 1e-20], False),  # squares to a float32 subnormal, not to zero
        ],
    )
    def test_recognises_the_origin(self, vector: list[float], expected: bool):
        assert has_zero_norm(vector) is expected

    @pytest.mark.parametrize("component", [1e-30, 1e-200, 5e-24])
    def test_a_norm_that_survives_float64_and_dies_in_float32_is_zero(self, component: float):
        """The band this function exists for.

        `1e-30` squares to `1e-60`: a normal float64, and zero in float32.
        Every adapter here stores float32 (`ports/vector_store.py` says so),
        so a vector whose norm is zero *there* is one no backend can score,
        whatever float64 makes of it. Checking the float64 norm alone would
        accept the first and third of these and leave pgvector returning NaN.
        """
        assert has_zero_norm([component] * 8)

    @pytest.mark.parametrize("component", [1e30, -1e30, 1e300, -1e300])
    def test_a_component_too_large_to_square_is_not_zero(self, component: float):
        """The overflow half of the same arithmetic.

        `1e30` is an ordinary float32 whose *square* is not one, and `1e300`
        is beyond float32 entirely. `struct.pack("f", ...)` raises
        `OverflowError` rather than returning an infinity, so a check that
        squared unconditionally would reject a large but perfectly good vector
        with the wrong exception type -- and `1e300` would not even reach the
        squaring. Both are as far from a zero norm as a float gets.
        """
        assert not has_zero_norm([component] * 4)

    def test_one_surviving_component_is_enough(self):
        """The check is about the vector's norm, not about every component:
        a single component that squares to something float32 can hold gives
        the vector a direction, however many others underflow."""
        assert not has_zero_norm([1e-30, 1e-30, 1.0])


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
